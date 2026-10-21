import requests

from .base import KeyedAdapter

ENDPOINT = 'https://api.telnyx.com/v2/number_lookup/%s'

LINE_TYPES = {
    'mobile': 'mobile',
    'fixed line': 'fixed',
    'voip': 'voip',
    'toll free': 'toll_free',
    'premium rate': 'premium',
    'shared cost': 'shared_cost',
}


class TelnyxLookup(KeyedAdapter):
    """Telnyx Number Lookup, carrier and caller name. Billed per request."""

    code = 'telnyx_lookup'
    param = 'connect_caller_intel.telnyx_api_key'

    def lookup(self, env, e164):
        try:
            data = self._get(
                ENDPOINT % e164, params=[('type', 'carrier'), ('type', 'caller-name')],
                headers={'Authorization': 'Bearer %s' % self._key(env)})
        except requests.HTTPError:
            return {'found': False, 'marks': []}
        info = data.get('data') or {}
        carrier = info.get('carrier') or {}
        cnam = (info.get('caller_name') or {}).get('caller_name')
        result = {'found': bool(carrier.get('name') or cnam), 'marks': []}
        if carrier.get('name'):
            result['carrier'] = carrier['name']
        if carrier.get('type'):
            result['line_type'] = LINE_TYPES.get(carrier['type'].lower(), 'unknown')
        if cnam:
            result['cnam'] = cnam
        return result
