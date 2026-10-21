import logging
import re

import phonenumbers

from .base import Adapter, AdapterError

_logger = logging.getLogger(__name__)

ENDPOINT = 'https://places.googleapis.com/v1/places:searchText'
FIELD_MASK = ','.join((
    'places.id', 'places.displayName', 'places.formattedAddress', 'places.internationalPhoneNumber'))
MAP_URL = 'https://www.google.com/maps/place/?q=place_id:%s'
CONFIDENCE = 0.7
TTL_HOURS = 720


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


def _digits(value):
    return re.sub(r'\D', '', value or '')


class GooglePlaces(KeyedAdapter):
    """Google Places (New) text search. Billed per request at the Enterprise tier ($35 per 1,000).

    The phone field is what proves the place owns the number, and it is an Enterprise field.
    """

    code = 'google_places'
    param = 'connect_caller_intel.google_places_api_key'

    def lookup(self, env, e164):
        try:
            region = phonenumbers.region_code_for_number(phonenumbers.parse(e164))
        except phonenumbers.NumberParseException:
            region = None
        body = {'textQuery': e164}
        if region:
            body['regionCode'] = region
        data = self._post(ENDPOINT, json=body, headers={
            'X-Goog-Api-Key': self._key(env), 'X-Goog-FieldMask': FIELD_MASK})
        wanted = _digits(e164)
        for place in data.get('places') or []:
            if _digits(place.get('internationalPhoneNumber')) != wanted:
                continue
            return {'found': True, 'marks': [], 'identities': [{
                'name': (place.get('displayName') or {}).get('text') or place['id'],
                'kind': 'business',
                'address': place.get('formattedAddress'),
                'url': MAP_URL % place['id'],
                'external_ref': place['id'],
                'confidence': CONFIDENCE,
                'ttl_hours': TTL_HOURS,
            }]}
        return {'found': False, 'marks': []}
