"""The Textable wire contract, tested on the client alone.

Spec: Textable OpenAPI 5.29.16@85ffb089 (sha256 d4202d60...1dda).
- UserToken auth is ``Authorization: Bearer accountUid:apiKey`` (spec.json:70-78).
- ``POST /api/send`` requires ``to``, ``from``, ``message`` (spec.json:2696-2717)
  and answers 200, 400, 404 or 500 with no body schema (spec.json:2797-2811).
- ``GET /api/canned-responses`` is scoped to the token's user (spec.json:2635).
Decisions D1 (body exactly {to, from, message}; sendStatus, type, sendAt,
reminder and media refused; status code only), D6 (no sendAt / reminder)
and F12 (requests, never httpx).
"""
import ast
import hashlib
import json
import os
import socket
import ssl
from unittest.mock import patch

import requests
from urllib3.exceptions import (
    MaxRetryError, NameResolutionError, NewConnectionError, ProtocolError,
    SSLError as Urllib3SSLError)

from odoo.tests import tagged
from odoo.tests.common import BaseCase, BlockedRequest
from odoo.tools.misc import file_open, file_path

from odoo.addons.connect_textable.tools import textable_client as tc

from .common import CONTACT, FakeTextable, NOT_NANP, SENDER_A, TENANT_URL

SPEC_SHA256 = 'd4202d605ba2764ebe551a3e008d69551a957da9f01de251480ac55db8fd1dda'
SPEC_VERSION = '5.29.16@85ffb089'
# sha256 of tests/fixtures/textable_send_request.json. A change to the
# request fixture is a change to the wire contract and needs a T1 review.
REQUEST_FIXTURE_SHA256 = '16d3baf76702624e3f95d1b84636017504950cac748056846d09b10b2c729417'
FIXTURE_PATH = 'connect_textable/tests/fixtures/textable_send_request.json'

UID = 'acct-client-test'
KEY = 'key-client-test-SECRET'
BODY = 'Your code is 4242.'


def _client():
    return tc.TextableClient(TENANT_URL, UID, KEY)


@tagged('post_install', '-at_install')
class TestTextableWireContract(BaseCase):
    """Request shape, pinned against the request fixture and the spec."""

    def test_spec_constants_are_pinned(self):
        """The client names the spec it was written against, by version and
        by the sha256 of the published document."""
        self.assertEqual(tc.TEXTABLE_SPEC_VERSION, SPEC_VERSION)
        self.assertEqual(tc.TEXTABLE_SPEC_SHA256, SPEC_SHA256)
        self.assertEqual(tc.SEND_PATH, '/api/send')
        self.assertEqual(tc.SEND_FIELDS, frozenset({'to', 'from', 'message'}))

    def test_request_fixture_is_pinned(self):
        """The committed request fixture has not changed and names the same
        spec as the client."""
        with file_open(FIXTURE_PATH, 'rb') as handle:
            raw = handle.read()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), REQUEST_FIXTURE_SHA256)
        fixture = json.loads(raw)
        self.assertEqual(fixture['spec_version'], SPEC_VERSION)
        self.assertEqual(fixture['spec_sha256'], SPEC_SHA256)

    def test_send_request_matches_fixture_exactly(self):
        """One POST to /api/send with the Bearer accountUid:apiKey header
        and a JSON body that is exactly {to, from, message}."""
        with file_open(FIXTURE_PATH, 'rb') as handle:
            fixture = json.loads(handle.read())
        client = tc.TextableClient(
            fixture['base_url'], fixture['account_uid'], fixture['api_key'])
        fake = FakeTextable(200)
        with fake.installed():
            result = client.send(
                fixture['json']['from'], fixture['json']['to'], fixture['json']['message'])
        self.assertEqual(len(fake.calls), 1)
        request = fake.calls[0].request
        self.assertEqual(request.method, fixture['method'])
        self.assertEqual(request.url, fixture['url'])
        for name, value in fixture['headers'].items():
            self.assertEqual(request.headers.get(name), value, name)
        self.assertNotIn('Cookie', request.headers)
        self.assertEqual(json.loads(request.body), fixture['json'])
        self.assertEqual(sorted(json.loads(request.body)), ['from', 'message', 'to'])
        self.assertEqual(result, tc.TransportResult('accepted', 200, result.detail))

    def test_send_transport_settings(self):
        """No redirects (the token would follow them), TLS verification on,
        a bounded (connect, read) timeout and no proxy from the
        environment."""
        fake = FakeTextable(200)
        with patch.dict(os.environ, {
                'HTTPS_PROXY': 'http://127.0.0.1:9', 'https_proxy': 'http://127.0.0.1:9',
                'ALL_PROXY': 'http://127.0.0.1:9'}), fake.installed():
            _client().send(SENDER_A, CONTACT, BODY)
        self.assertEqual(len(fake.calls), 1)
        kwargs = fake.calls[0].kwargs
        self.assertIs(kwargs['allow_redirects'], False)
        self.assertEqual(kwargs['timeout'], (3, 10))
        self.assertIs(kwargs['verify'], True)
        self.assertFalse(kwargs.get('proxies'))

    def test_response_body_is_never_read(self):
        """Parsing uses the status code only: the body is not read, and the
        response is closed."""
        for status in (200, 400, 404, 500):
            with self.subTest(status=status):
                fake = FakeTextable(status)
                with fake.installed():
                    _client().send(SENDER_A, CONTACT, BODY)
                self.assertEqual(len(fake.calls), 1)
                self.assertEqual(fake.bodies[0].reads, 0)
                self.assertTrue(fake.bodies[0].closed)

    def test_probe_is_one_get_with_the_same_token(self):
        """The connection test is one read-only GET scoped to the token's
        own user, with the same Authorization header and no body."""
        fake = FakeTextable(200)
        with fake.installed():
            result = _client().probe()
        self.assertEqual(len(fake.calls), 1)
        request = fake.calls[0].request
        self.assertEqual(request.method, 'GET')
        self.assertEqual(request.url, TENANT_URL + '/api/canned-responses')
        self.assertEqual(request.headers['Authorization'], 'Bearer %s:%s' % (UID, KEY))
        self.assertFalse(request.body)
        self.assertEqual(result.outcome, 'accepted')

    def test_body_refuses_every_field_but_to_from_message(self):
        """sendStatus, type, sendAt, reminder, scheduleTimezone and media are
        refused by name (D1, D6), and so is any other optional field of the
        spec (notify, autoArchive)."""
        for field, value in (
                ('sendStatus', 'sent'), ('type', 'note'), ('sendAt', 2880544055000),
                ('reminder', True), ('scheduleTimezone', 'America/Chicago'),
                ('media', ['https://cdn.example.com/a.jpg']), ('notify', True),
                ('autoArchive', True)):
            with self.subTest(field=field):
                with self.assertRaises(tc.TextableError):
                    tc.build_send_body(SENDER_A, CONTACT, BODY, **{field: value})
        self.assertEqual(
            tc.build_send_body(SENDER_A, CONTACT, BODY),
            {'to': CONTACT, 'from': SENDER_A, 'message': BODY})

    def test_body_requires_nanp_numbers_and_a_bounded_message(self):
        """Both numbers are NANP E.164, the message is not blank and at most
        MAX_MESSAGE_LENGTH characters."""
        refused = (
            (NOT_NANP, CONTACT, BODY),
            (SENDER_A, NOT_NANP, BODY),
            (SENDER_A, '2025550147', BODY),
            (SENDER_A, '+12025550147 ', BODY),
            (SENDER_A, '+11025550147', BODY),
            (SENDER_A, CONTACT, ''),
            (SENDER_A, CONTACT, '   '),
            (SENDER_A, CONTACT, 'x' * (tc.MAX_MESSAGE_LENGTH + 1)),
        )
        for sender, recipient, message in refused:
            with self.subTest(sender=sender, recipient=recipient, length=len(message)):
                with self.assertRaises(tc.TextableError):
                    tc.build_send_body(sender, recipient, message)
        self.assertEqual(
            len(tc.build_send_body(SENDER_A, CONTACT, 'x' * tc.MAX_MESSAGE_LENGTH)['message']),
            tc.MAX_MESSAGE_LENGTH)

    def test_refused_request_makes_no_http_call(self):
        """A request the client refuses raises TextableError before any
        request leaves."""
        fake = FakeTextable()
        with fake.installed(), self.assertRaises(tc.TextableError):
            _client().send(SENDER_A, NOT_NANP, BODY)
        self.assertEqual(len(fake.calls), 0)

    def test_repr_hides_credentials(self):
        """Neither the account UID nor the API key shows in the client's
        repr or str."""
        client = _client()
        for text in (repr(client), str(client)):
            self.assertNotIn(UID, text)
            self.assertNotIn(KEY, text)

    def test_credentials_are_checked(self):
        """Empty halves, a colon in the account UID (it would split the
        token) and whitespace are refused before any request."""
        for uid, key in (('', KEY), (UID, ''), ('acct:extra', KEY),
                         ('acct uid', KEY), (UID, 'key with space'), (UID, 'key\n')):
            with self.subTest(uid=uid, key=key):
                with self.assertRaises(tc.TextableError):
                    tc.TextableClient(TENANT_URL, uid, key)


@tagged('post_install', '-at_install')
class TestTextableBaseUrl(BaseCase):
    """Credentials go only to https://<label>.textable.app on port 443."""

    def test_accepted_forms_normalize_to_the_bare_host(self):
        for raw in ('https://example-tenant.textable.app',
                    'https://example-tenant.textable.app/',
                    'https://example-tenant.textable.app:443',
                    '  https://EXAMPLE-TENANT.textable.app  '):
            with self.subTest(raw=raw):
                self.assertEqual(tc.normalize_base_url(raw), TENANT_URL)

    def test_everything_else_is_refused(self):
        for raw in (None, '', 'example-tenant.textable.app',
                    'http://example-tenant.textable.app',
                    'https://example-tenant.textable.app:8443',
                    'https://example-tenant.textable.app/api',
                    'https://example-tenant.textable.app?x=1',
                    'https://example-tenant.textable.app#frag',
                    'https://user:pw@example-tenant.textable.app',
                    'https://example-tenant.textable.app\\@evil.example.com',
                    'https://textable.app',
                    'https://a.b.textable.app',
                    'https://example-tenant.textable.app.evil.example.com',
                    'https://evil.example.com',
                    'ftp://example-tenant.textable.app'):
            with self.subTest(raw=raw):
                with self.assertRaises(tc.TextableError):
                    tc.normalize_base_url(raw)


@tagged('post_install', '-at_install')
class TestTextableOutcomes(BaseCase):
    """Every answer maps to one outcome; only accepted and ambiguous may
    have reached the carrier (F2)."""

    def _send_with(self, answer):
        fake = FakeTextable(answer)
        with fake.installed():
            result = _client().send(SENDER_A, CONTACT, BODY)
        self.assertEqual(len(fake.calls), 1)
        return result

    def test_status_codes(self):
        expected = {
            200: 'accepted',
            400: 'rejected_request',
            404: 'rejected_contact',
            401: 'auth_failed',
            403: 'auth_failed',
            429: 'throttled',
            301: 'rejected_request',
            302: 'rejected_request',
            307: 'rejected_request',
            500: 'ambiguous',
            502: 'ambiguous',
            503: 'ambiguous',
            504: 'ambiguous',
        }
        for status, outcome in expected.items():
            with self.subTest(status=status):
                result = self._send_with(status)
                self.assertEqual(result.outcome, outcome)
                self.assertEqual(result.http_status, status)

    def test_transport_errors(self):
        """A connection that provably never opened is unreachable. A read
        timeout, a dropped connection after the request went out, or any
        unclassified error is ambiguous, Odoo's BlockedRequest included."""
        cases = (
            (requests.exceptions.ConnectTimeout('connect timed out'), 'unreachable'),
            (requests.exceptions.ConnectionError(MaxRetryError(
                None, '/api/send', NewConnectionError(None, 'Failed to establish a new connection'))),
             'unreachable'),
            (requests.exceptions.ConnectionError(MaxRetryError(
                None, '/api/send', NameResolutionError(
                    'example-tenant.textable.app', None, socket.gaierror(-2, 'Name or service not known')))),
             'unreachable'),
            (requests.exceptions.ReadTimeout('read timed out'), 'ambiguous'),
            (requests.exceptions.ConnectionError(ProtocolError(
                'Connection aborted.', ConnectionResetError(104, 'reset'))), 'ambiguous'),
            (BlockedRequest('External requests verboten (was POST %s/api/send)' % TENANT_URL),
             'ambiguous'),
            (RuntimeError('unexpected'), 'ambiguous'),
        )
        for exc, outcome in cases:
            with self.subTest(exc=type(exc).__name__, outcome=outcome):
                result = self._send_with(exc)
                self.assertEqual(result.outcome, outcome)
                self.assertIsNone(result.http_status)
                self.assertNotEqual(result.outcome, 'accepted')

    def test_certificate_failure_is_unreachable(self):
        """A certificate that fails verification stops the request before
        any byte of it is sent."""
        error = requests.exceptions.SSLError(MaxRetryError(
            None, '/api/send', Urllib3SSLError(
                ssl.SSLCertVerificationError(1, 'certificate verify failed'))))
        self.assertEqual(self._send_with(error).outcome, 'unreachable')

    def test_error_detail_carries_no_payload(self):
        """The detail of a transport error names the error type only, never
        text from the exception (which may echo the number or the body)."""
        leaky = '%s %s %s' % (CONTACT, BODY, KEY)
        for exc in (requests.exceptions.ReadTimeout(leaky),
                    requests.exceptions.ConnectionError(leaky),
                    requests.exceptions.ConnectTimeout(leaky),
                    ValueError(leaky)):
            with self.subTest(exc=type(exc).__name__):
                detail = self._send_with(exc).detail
                self.assertNotIn(CONTACT, detail)
                self.assertNotIn(BODY, detail)
                self.assertNotIn(KEY, detail)

    def test_maybe_sent_outcomes(self):
        """Only accepted and ambiguous may have reached the carrier."""
        self.assertEqual(tc.MAYBE_SENT_OUTCOMES, frozenset({'accepted', 'ambiguous'}))
        self.assertEqual(set(tc.OUTCOMES), {
            'accepted', 'rejected_request', 'rejected_contact', 'auth_failed',
            'throttled', 'unreachable', 'ambiguous'})


@tagged('post_install', '-at_install')
class TestTextableNoHttpx(BaseCase):
    """F12: Odoo's test guard patches requests only, so the carrier never
    imports httpx (an httpx call would bypass BlockedRequest)."""

    def test_module_never_imports_httpx(self):
        root = os.path.dirname(file_path('connect_textable/__manifest__.py'))
        offenders = []
        for folder, _dirs, files in os.walk(root):
            for name in files:
                if not name.endswith('.py'):
                    continue
                path = os.path.join(folder, name)
                with open(path, encoding='utf-8') as handle:
                    tree = ast.parse(handle.read(), filename=path)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        modules = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        modules = [node.module or '']
                    else:
                        continue
                    if any(m == 'httpx' or m.startswith('httpx.') for m in modules):
                        offenders.append(os.path.relpath(path, root))
        self.assertEqual(offenders, [])
        self.assertIs(tc.requests, requests)
