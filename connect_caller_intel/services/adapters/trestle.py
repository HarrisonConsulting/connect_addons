import requests

from .google_places import KeyedAdapter

ENDPOINT = 'https://api.trestleiq.com/3.1/caller_id'
CONFIDENCE = 0.5
# The terms forbid storing a result to avoid a repeat query. The source's own ttl is 0, so every
# call queries again; the hint only lives long enough for the call card that asked for it.
CALL_SCOPED_HOURS = 1


def _address(item):
    street = item.get('street_line_1')
    locality = ' '.join(filter(None, [item.get('state_code'), item.get('postal_code')]))
    return ', '.join(filter(None, [street, item.get('city'), locality])) or False


class Trestle(KeyedAdapter):
    """Trestle Caller Identification. Billed per request, never reused, never written to a contact."""

    code = 'trestle'
    param = 'connect_caller_intel.trestle_api_key'
    nanp_only = True

    def lookup(self, env, e164):
        try:
            data = self._get(
                ENDPOINT, params={'phone': e164[2:], 'phone.country_hint': 'US'},
                headers={'x-api-key': self._key(env)})
        except requests.HTTPError:
            return {'found': False, 'marks': []}
        owner = data.get('belongs_to') or {}
        if isinstance(owner, list):
            owner = owner[0] if owner else {}
        if not owner.get('name'):
            return {'found': False, 'marks': []}
        addresses = data.get('current_addresses') or []
        current = next((a for a in addresses if a.get('is_active') is not False), None)
        return {'found': True, 'marks': [], 'identities': [{
            'name': owner['name'],
            'kind': {'Person': 'person', 'Business': 'business'}.get(owner.get('type'), 'unknown'),
            'address': _address(current) if current else False,
            'external_ref': 'trestle:' + e164,
            'confidence': CONFIDENCE,
            'ttl_hours': CALL_SCOPED_HOURS,
        }]}
