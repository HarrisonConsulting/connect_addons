import logging
from datetime import datetime

import requests

_logger = logging.getLogger(__name__)

HTTP_TIMEOUT = 10
_warned = set()


class AdapterError(Exception):
    """Transient failure; the job is retried."""


class AdapterThrottled(AdapterError):
    """The source is paced; retry after ``seconds`` without counting a failure."""

    def __init__(self, msg, seconds=1):
        super().__init__(msg)
        self.seconds = seconds


class Adapter:
    """One external reputation source.

    ``lookup`` returns ``{'found': bool, 'marks': [mark dicts], ...}`` where a mark dict
    carries reporter, category, report_count, first_reported, last_reported, evidence_url
    and external_ref. Line facts (``line_type``, ``carrier``, ``cnam``) ride beside it.
    """

    code = None
    batch = False
    ready = True
    nanp_only = False

    def applies_to(self, env, rep):
        """True when this source can answer for ``rep``; NANP-only sources skip other countries."""
        return not self.nanp_only or rep.e164.startswith('+1')

    def credential(self, env):
        """Return the credential for this adapter, or None when it is not configured."""
        return True

    def key_hint(self):
        return self.code

    def available(self, env):
        """True when the credential exists; warns once per process when it does not."""
        if self.credential(env):
            return True
        if self.code not in _warned:
            _warned.add(self.code)
            _logger.warning(
                "Caller intel source %s is unavailable: set %s", self.code, self.key_hint())
        return False

    def lookup(self, env, e164):
        raise NotImplementedError

    def _request(self, method, url, **kwargs):
        try:
            response = method(url, timeout=HTTP_TIMEOUT, **kwargs)
        except requests.RequestException as exc:
            raise AdapterError(str(exc)) from exc
        if response.status_code in (401, 403):
            raise AdapterError("%s rejected the credential (%s)" % (self.code, response.status_code))
        if response.status_code == 429 or response.status_code >= 500:
            raise AdapterError("%s returned %s" % (self.code, response.status_code))
        response.raise_for_status()
        return response

    def _get(self, url, **kwargs):
        return self._request(requests.get, url, **kwargs).json()

    def _post(self, url, **kwargs):
        return self._request(requests.post, url, **kwargs).json()

    def _get_text(self, url, **kwargs):
        return self._request(requests.get, url, **kwargs).text


def polite_user_agent(env):
    """User agent that names this system and a contact, as the polite public sources ask."""
    get_param = env['ir.config_parameter'].sudo().get_param
    contact = get_param('connect_caller_intel.http_contact') or get_param('web.base.url')
    return 'HarrisonConnectCallerIntel/19.0 (+%s)' % contact


def parse_dt(value):
    """Naive UTC datetime from an ISO string, or None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '')[:19])
    except ValueError:
        return None


def reset_warnings():
    _warned.clear()


class KeyedAdapter(Adapter):
    """Adapter whose customer-supplied key lives in an ``ir.config_parameter``.

    The key is read at call time and never logged; a rejected key logs one warning that names
    the parameter to fix.
    """

    param = None

    def _key(self, env):
        return env['ir.config_parameter'].sudo().get_param(self.param) or None

    def credential(self, env):
        return self._key(env)

    def key_hint(self):
        return self.param

    def _request(self, method, url, **kwargs):
        try:
            return super()._request(method, url, **kwargs)
        except AdapterError as exc:
            if 'rejected the credential' in str(exc):
                _logger.warning("Caller intel source %s: the key in %s was rejected",
                                self.code, self.param)
            raise
