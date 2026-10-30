"""Textable REST client.

Pure Python on ``requests``: nothing here imports Odoo, so the wire rules
can be tested without a database. The client never reads a response body
and never puts a response body, a message text, a full phone number or a
credential into an exception or a log line.

Wire contract: Textable spec 5.29.16@85ffb089 (see TEXTABLE_SPEC_*).
``POST /api/send`` takes ``{to, from, message}`` and answers 200, 400,
404 or 500 with no message id; the only id source is the relay webhook.
"""
import logging
import re
import ssl
from collections import namedtuple
from urllib.parse import urlsplit

import requests
from urllib3.exceptions import ConnectTimeoutError, NewConnectionError

_logger = logging.getLogger(__name__)

TEXTABLE_SPEC_VERSION = '5.29.16@85ffb089'
TEXTABLE_SPEC_SHA256 = (
    'd4202d605ba2764ebe551a3e008d69551a957da9f01de251480ac55db8fd1dda')

SEND_PATH = '/api/send'
# Read-only and scoped to the token's own user (spec.json:2635).
PROBE_PATH = '/api/canned-responses'

# (connect, read) seconds.
TIMEOUT = (3, 10)

# Textable's own limit is not documented; 1600 is the common carrier
# ceiling for a concatenated SMS.
MAX_MESSAGE_LENGTH = 1600

SEND_FIELDS = frozenset({'to', 'from', 'message'})
# Optional /api/send fields this client refuses outright: Odoo is the only
# scheduler (sendAt, reminder), sendStatus and type store a message without
# sending it, and MMS is out of scope.
FORBIDDEN_FIELDS = frozenset({
    'sendStatus', 'type', 'sendAt', 'reminder', 'scheduleTimezone', 'media',
})

NANP_E164 = re.compile(r'^\+1[2-9]\d{2}[2-9]\d{6}$')
HOST_RE = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.textable\.app$')
_UNSAFE_URL_CHARS = re.compile(r'[\s\\\x00-\x1f\x7f@?#]')
_UNSAFE_CREDENTIAL_CHARS = re.compile(r'[\s\x00-\x1f\x7f]')

ACCEPTED = 'accepted'
REJECTED_REQUEST = 'rejected_request'
REJECTED_CONTACT = 'rejected_contact'
AUTH_FAILED = 'auth_failed'
THROTTLED = 'throttled'
UNREACHABLE = 'unreachable'
AMBIGUOUS = 'ambiguous'

OUTCOMES = (
    ACCEPTED, REJECTED_REQUEST, REJECTED_CONTACT, AUTH_FAILED, THROTTLED,
    UNREACHABLE, AMBIGUOUS,
)
# Outcomes after which Textable may hold the message. A send with one of
# these is never submitted again.
MAYBE_SENT_OUTCOMES = frozenset({ACCEPTED, AMBIGUOUS})

TransportResult = namedtuple('TransportResult', ['outcome', 'http_status', 'detail'])


class TextableError(Exception):
    """A request this client refuses to make. The text is safe to log."""


def last4(number):
    """The last four digits of a number, for logs and messages."""
    digits = re.sub(r'\D', '', number or '')
    return '…' + digits[-4:] if digits else '…'


def is_nanp_e164(number):
    return bool(isinstance(number, str) and NANP_E164.match(number))


def normalize_base_url(raw):
    """``https://<label>.textable.app`` or TextableError.

    The URL is rebuilt from the parsed host, so a port, a path, a query,
    userinfo or a backslash in the stored value cannot reach the request.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise TextableError('The Textable base URL is not set.')
    value = raw.strip()
    if value.endswith('/'):
        value = value[:-1]
    if _UNSAFE_URL_CHARS.search(value):
        raise TextableError(
            'The Textable base URL may not contain spaces, backslashes, '
            'userinfo, a query or a fragment.')
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise TextableError('The Textable base URL cannot be parsed.') from exc
    if parts.scheme != 'https':
        raise TextableError('The Textable base URL must use https.')
    if port not in (None, 443):
        raise TextableError('The Textable base URL must use port 443.')
    if parts.path or parts.query or parts.fragment:
        raise TextableError('The Textable base URL must not carry a path.')
    host = (parts.hostname or '').lower()
    if not HOST_RE.match(host):
        raise TextableError(
            'The Textable base URL must be https://<label>.textable.app.')
    return 'https://%s' % host


def _check_credentials(account_uid, api_key):
    if not isinstance(account_uid, str) or not account_uid:
        raise TextableError('The Textable account UID is not set.')
    if not isinstance(api_key, str) or not api_key:
        raise TextableError('The Textable API key is not set.')
    if ':' in account_uid or _UNSAFE_CREDENTIAL_CHARS.search(account_uid):
        raise TextableError(
            'The Textable account UID may not contain a colon or whitespace.')
    if _UNSAFE_CREDENTIAL_CHARS.search(api_key):
        raise TextableError('The Textable API key may not contain whitespace.')
    if len(account_uid) > 256 or len(api_key) > 1024:
        raise TextableError('The Textable credentials are too long.')


def build_send_body(sender, recipient, message, **extra):
    """The /api/send body: exactly ``{to, from, message}``.

    Any other field is refused; sendStatus, type, sendAt, reminder and
    media by name.
    """
    refused = sorted(FORBIDDEN_FIELDS.intersection(extra))
    if refused:
        raise TextableError(
            'Refused Textable send field(s): %s.' % ', '.join(refused))
    if extra:
        raise TextableError(
            'Unknown Textable send field(s): %s.' % ', '.join(sorted(extra)))
    if not is_nanp_e164(sender):
        raise TextableError('The sender is not a NANP E.164 number.')
    if not is_nanp_e164(recipient):
        raise TextableError('The recipient is not a NANP E.164 number.')
    if not isinstance(message, str) or not message.strip():
        raise TextableError('The message is empty.')
    if len(message) > MAX_MESSAGE_LENGTH:
        raise TextableError(
            'The message is longer than %d characters.' % MAX_MESSAGE_LENGTH)
    body = {'to': recipient, 'from': sender, 'message': message}
    assert set(body) == SEND_FIELDS
    return body


def classify_status(status):
    if 200 <= status < 300:
        return ACCEPTED
    if 300 <= status < 400:
        # Redirects are never followed: the credential would go with them.
        return REJECTED_REQUEST
    if status in (401, 403):
        return AUTH_FAILED
    if status == 404:
        # "error with the user or contact" (spec.json /api/send 404).
        return REJECTED_CONTACT
    if status == 429:
        return THROTTLED
    if 400 <= status < 500:
        return REJECTED_REQUEST
    # 5xx "failed to send" may still have reached the carrier, and
    # anything else is not a documented answer.
    return AMBIGUOUS


def _exception_chain(exc, limit=12):
    seen = set()
    todo = [exc]
    while todo and len(seen) < limit:
        current = todo.pop(0)
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        todo.extend([
            getattr(current, 'reason', None),
            current.__cause__,
            current.__context__,
        ])
        todo.extend(arg for arg in current.args if isinstance(arg, BaseException))


def classify_exception(exc):
    """unreachable only when the request provably never left: a connect
    timeout, a refused connection, a DNS failure or a certificate that
    failed verification. Everything else, a read timeout included, is
    ambiguous."""
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return AMBIGUOUS
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return UNREACHABLE
    for link in _exception_chain(exc):
        if isinstance(link, (ConnectTimeoutError, NewConnectionError,
                             ssl.SSLCertVerificationError)):
            return UNREACHABLE
    return AMBIGUOUS


def _describe_exception(exc, outcome):
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return 'read timeout'
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return 'connect timeout'
    if outcome == UNREACHABLE:
        return 'connection failed (%s)' % type(exc).__name__
    return 'transport error (%s)' % type(exc).__name__


class TextableClient:
    """One Textable user's credentials against one tenant host."""

    def __init__(self, base_url, account_uid, api_key):
        self.base_url = normalize_base_url(base_url)
        _check_credentials(account_uid, api_key)
        self.__authorization = 'Bearer %s:%s' % (account_uid, api_key)

    def __repr__(self):
        return '<TextableClient %s>' % self.base_url

    def _session(self):
        session = requests.Session()
        # No proxy, netrc or CA bundle from the environment.
        session.trust_env = False
        session.headers.update({
            'Accept': 'application/json',
            'User-Agent': 'odoo-connect_textable',
        })
        return session

    def _request(self, method, path, json_body=None):
        url = self.base_url + path
        headers = {'Authorization': self.__authorization}
        with self._session() as session:
            try:
                response = session.request(
                    method, url, json=json_body, headers=headers,
                    timeout=TIMEOUT, allow_redirects=False, stream=True)
            except Exception as exc:  # noqa: BLE001 - every failure is classified
                outcome = classify_exception(exc)
                return TransportResult(outcome, None, _describe_exception(exc, outcome))
            try:
                status = response.status_code
            finally:
                # The body is never read: it is not logged, not stored and
                # not needed to classify the answer.
                response.close()
        return TransportResult(classify_status(status), status, 'HTTP %d' % status)

    def send(self, sender, recipient, message):
        """POST /api/send. Returns a TransportResult; raises TextableError
        only for a request it refuses to make."""
        body = build_send_body(sender, recipient, message)
        return self._request('POST', SEND_PATH, body)

    def probe(self):
        """One read-only GET that proves the host and the token."""
        return self._request('GET', PROBE_PATH)
