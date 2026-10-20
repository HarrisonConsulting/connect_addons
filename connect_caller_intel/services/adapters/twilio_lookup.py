import requests

from .base import HTTP_TIMEOUT, Adapter, AdapterError

ENDPOINT = 'https://lookups.twilio.com/v2/PhoneNumbers/%s'

LINE_TYPES = {
    'mobile': 'mobile',
    'landline': 'fixed',
    'fixedVoip': 'voip',
    'nonFixedVoip': 'voip',
    'tollFree': 'toll_free',
    'premium': 'premium',
    'sharedCost': 'shared_cost',
}


class TwilioLookup(Adapter):
    """Twilio Lookup v2 (paid per request). Never called unless its source is enabled."""

    batch = False
    fields = None

    def __init__(self, code, fields):
        self.code = code
        self.fields = fields

    def _auth(self, env):
        settings = env['connect.settings']
        sid = settings.get_param('account_sid')
        token = settings.get_param('auth_token')
        return (sid, token) if sid and token else None

    def credential(self, env):
        return self._auth(env)

    def key_hint(self):
        return 'connect.settings account_sid and auth_token'

    def lookup(self, env, e164):
        try:
            response = requests.get(
                ENDPOINT % e164, params={'Fields': self.fields}, auth=self._auth(env),
                timeout=HTTP_TIMEOUT)
        except requests.RequestException as exc:
            raise AdapterError(str(exc)) from exc
        if response.status_code == 404:
            return {'found': False, 'marks': []}
        if response.status_code in (401, 403, 429) or response.status_code >= 500:
            raise AdapterError('%s returned %s' % (self.code, response.status_code))
        response.raise_for_status()
        data = response.json()
        result = {'found': True, 'marks': []}
        lti = data.get('line_type_intelligence') or {}
        if lti:
            result['line_type'] = LINE_TYPES.get(lti.get('type'), 'unknown')
            result['carrier'] = lti.get('carrier_name') or False
        cnam = data.get('caller_name') or {}
        if cnam.get('caller_name'):
            result['cnam'] = cnam['caller_name']
        result['found'] = bool(lti or cnam.get('caller_name'))
        return result
