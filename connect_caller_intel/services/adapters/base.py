import logging
from datetime import datetime

import requests

_logger = logging.getLogger(__name__)

HTTP_TIMEOUT = 10
_warned = set()


class AdapterError(Exception):
    """Transient failure; the job is retried."""


class Adapter:
    """One external reputation source.

    ``lookup`` returns ``{'found': bool, 'marks': [mark dicts], ...}`` where a mark dict
    carries reporter, category, report_count, first_reported, last_reported, evidence_url
    and external_ref. Line facts (``line_type``, ``carrier``, ``cnam``) ride beside it.
    """

    code = None
    batch = False

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

    def _get(self, url, **kwargs):
        try:
            response = requests.get(url, timeout=HTTP_TIMEOUT, **kwargs)
        except requests.RequestException as exc:
            raise AdapterError(str(exc)) from exc
        if response.status_code in (401, 403):
            raise AdapterError("%s rejected the credential (%s)" % (self.code, response.status_code))
        if response.status_code == 429 or response.status_code >= 500:
            raise AdapterError("%s returned %s" % (self.code, response.status_code))
        response.raise_for_status()
        return response.json()


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
