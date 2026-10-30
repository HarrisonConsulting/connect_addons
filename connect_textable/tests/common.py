"""Shared fixtures for the connect_textable tests.

Nothing here talks to a network. Textable is replaced at the transport
boundary, ``requests.Session.send``, the same method Odoo's BlockedRequest
guard patches: the client under test builds and prepares its request for
real, and the fake records the prepared request and the send arguments.
"""
import hashlib
import logging
from collections import namedtuple
from contextlib import contextmanager
from unittest.mock import patch
from uuid import uuid4

import requests

from odoo.tests import TransactionCase, new_test_user

TENANT_URL = 'https://example-tenant.textable.app'
ORG_ID = 'org-fixture-0001'

SENDER_A = '+12015550123'
SENDER_B = '+12015550124'
SENDER_C = '+12015550125'
CONTACT = '+12025550147'
CONTACT_2 = '+12025550188'
CONTACT_3 = '+12125550100'
NOT_NANP = '+442071838750'

TEXTABLE_USER_A = 'tx-user-alpha'
TEXTABLE_USER_B = 'tx-user-bravo'

TextableCall = namedtuple('TextableCall', ['request', 'kwargs'])


def sha256_hex(text):
    return hashlib.sha256(text.encode()).hexdigest()


def digits(number):
    return ''.join(ch for ch in number if ch.isdigit())


class UnreadBody:
    """A response body that counts every attempt to read it."""

    def __init__(self):
        self.reads = 0
        self.closed = False

    def read(self, *args, **kwargs):
        self.reads += 1
        return b'{"detail": "never read"}'

    def stream(self, *args, **kwargs):
        self.reads += 1
        return iter([b'{"detail": "never read"}'])

    def close(self):
        self.closed = True


class FakeTextable:
    """Stands in for the Textable host at ``requests.Session.send``.

    Each queued item is an HTTP status (answered with an empty-looking
    response whose body must never be read) or an exception to raise. A
    request beyond the queue fails the call loudly; every test also asserts
    the exact number of calls.
    """

    def __init__(self, *answers, on_request=None):
        self.answers = list(answers)
        self.calls = []
        self.bodies = []
        self.on_request = on_request

    def _send(self, session, request, **kwargs):
        self.calls.append(TextableCall(request, kwargs))
        if self.on_request:
            self.on_request(request)
        if not self.answers:
            raise AssertionError('Unexpected Textable request %s %s' % (request.method, request.url))
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        response = requests.Response()
        response.status_code = answer
        response.url = request.url
        response.request = request
        response.headers['Content-Type'] = 'application/json'
        if 300 <= answer < 400:
            response.headers['Location'] = 'https://elsewhere.example.com/api/send'
        response.raw = UnreadBody()
        self.bodies.append(response.raw)
        return response

    @contextmanager
    def installed(self):
        def send(session, request, **kwargs):
            return self._send(session, request, **kwargs)
        with patch.object(requests.Session, 'send', send):
            yield self


class _ListHandler(logging.Handler):

    def __init__(self, level):
        super().__init__(level)
        self.records = []

    def emit(self, record):
        self.records.append(record)


class CapturedLogs:
    """Records of one capture: ``module`` from connect_textable (kept out of
    the session log), ``other`` from every other logger at INFO and up."""

    _formatter = logging.Formatter()

    def __init__(self, module_records, other_records):
        self.module = module_records
        self.other = other_records

    @classmethod
    def _render(cls, record):
        text = record.getMessage()
        if record.exc_info:
            text += '\n' + cls._formatter.formatException(record.exc_info)
        if record.stack_info:
            text += '\n' + record.stack_info
        return text

    def texts(self):
        return [self._render(r) for r in self.module + self.other]

    def module_texts(self, level=None):
        return [self._render(r) for r in self.module if level is None or r.levelno == level]

    def assert_clean(self, testcase, *secrets):
        """No captured line contains any of the secrets."""
        texts = self.texts()
        for secret in secrets:
            if not secret:
                continue
            leaked = [t for t in texts if secret in t]
            testcase.assertFalse(
                leaked, 'A log line carries a value that must never be logged: %r' % leaked)


@contextmanager
def capture_logs():
    """Capture every connect_textable record at DEBUG, without letting it
    reach the session log, and every INFO+ record of any other logger.

    The relay webhook logs from the HTTP server thread; loggers are process
    wide, so those records are captured too.
    """
    module_logger = logging.getLogger('odoo.addons.connect_textable')
    root = logging.getLogger()
    module_handler = _ListHandler(logging.DEBUG)
    other_handler = _ListHandler(logging.INFO)
    saved = (module_logger.level, module_logger.propagate, list(module_logger.handlers))
    module_logger.handlers = [module_handler]
    module_logger.setLevel(logging.DEBUG)
    module_logger.propagate = False
    root.addHandler(other_handler)
    try:
        yield CapturedLogs(module_handler.records, other_handler.records)
    finally:
        root.removeHandler(other_handler)
        module_logger.handlers = saved[2]
        module_logger.setLevel(saved[0])
        module_logger.propagate = saved[1]


class TextableFixtureMixin:
    """Tenant settings, two approved numbers with their own Textable users
    and tokens, and a Connect user assigned to both."""

    @classmethod
    def _textable_fixtures(cls):
        cls.run_tag = uuid4().hex[:8]
        cls.uid_a = 'acct-alpha-%s' % cls.run_tag
        cls.key_a = 'key-alpha-%s-SECRET' % cls.run_tag
        cls.uid_b = 'acct-bravo-%s' % cls.run_tag
        cls.key_b = 'key-bravo-%s-SECRET' % cls.run_tag
        Settings = cls.env['connect.settings']
        Settings.set_param('textable_base_url', TENANT_URL)
        Settings.set_param('textable_organization_id', ORG_ID)
        cls.sender_user = new_test_user(
            cls.env, login='textable-sender-%s' % cls.run_tag,
            groups='base.group_user,connect.group_user')
        cls.connect_user = cls._connect_user(cls.sender_user)
        cls.number_a = cls._number(
            'Alpha line', SENDER_A, TEXTABLE_USER_A, cls.uid_a, cls.key_a)
        cls.number_b = cls._number(
            'Bravo line', SENDER_B, TEXTABLE_USER_B, cls.uid_b, cls.key_b)

    @classmethod
    def _connect_user(cls, odoo_user, provider='textable'):
        return cls.env['connect.user'].with_context(
            no_clear_cache=True, no_voicetel_create=True,
        ).create({'user': odoo_user.id, 'message_provider': provider})

    @classmethod
    def _number(cls, name, phone, textable_user, uid, key, approved=True,
                assigned=True, **extra):
        vals = {
            'name': name,
            'phone_number': phone,
            'textable_user_id': textable_user,
            'display_account_uid': uid,
            'display_api_key': key,
            'campaign_state': 'approved' if approved else 'not_approved',
            'campaign_reference': 'CAMP-%s' % name.split()[0].upper() if approved else False,
            'campaign_use_case': 'Account notifications',
        }
        if assigned:
            vals['user_ids'] = [(4, cls.connect_user.id)]
        vals.update(extra)
        return cls.env['connect.textable.number'].create(vals)

    def _intents(self, **filters):
        domain = [(name, '=', value) for name, value in filters.items()]
        return self.env['connect.textable.send'].sudo().search(domain)

    def _messages(self, **filters):
        domain = [(name, '=', value) for name, value in filters.items()]
        return self.env['connect.message'].sudo().search(domain)


class TextableSendCase(TextableFixtureMixin, TransactionCase):
    """Sends run with the registry in test mode: the intent and outcome
    cursors the send opens wrap this test's transaction, so what they commit
    is visible here and is rolled back with the test."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._textable_fixtures()

    def _send(self, recipient, body, fake, as_user=True, **kwargs):
        """connect.message.send() against the fake Textable host, as the
        assigned Connect user (``as_user=True``), as another res.users
        record, or as the superuser (``as_user=False``). The returned
        message is read as the superuser."""
        Message = self.env['connect.message']
        if as_user is True:
            Message = Message.with_user(self.sender_user)
        elif as_user:
            Message = Message.with_user(as_user)
        with self.enter_registry_test_mode(), fake.installed():
            message = Message.send(recipient, body, **kwargs)
        return message.sudo()
