import requests

from .base import AdapterError, KeyedAdapter

ENDPOINT = 'https://www.ipqualityscore.com/api/json/phone/%s/%s'
MARK_FROM_PARAM = 'connect_caller_intel.ipqs_mark_from'
DEFAULT_MARK_FROM = 85
SCAM_FROM = 90
REPORTER = 'IPQualityScore reputation'

LINE_TYPES = {
    'wireless': 'mobile',
    'mobile': 'mobile',
    'landline': 'fixed',
    'voip': 'voip',
    'toll free': 'toll_free',
    'premium rate': 'premium',
}


class Ipqs(KeyedAdapter):
    """IPQualityScore phone validation. Free allotment, then billed. The customer brings the key.

    The key travels in the URL path, so every error is scrubbed of it before it can be logged.
    """

    code = 'ipqs'
    param = 'connect_caller_intel.ipqs_api_key'

    def lookup(self, env, e164):
        key = self._key(env)
        rep = env['connect.caller.reputation'].sudo().search([('e164', '=', e164)], limit=1)
        params = {'strictness': 0}
        if rep.country_code:
            params['country[]'] = rep.country_code
        try:
            data = self._get(ENDPOINT % (key, e164), params=params)
        except (AdapterError, requests.HTTPError) as exc:
            raise AdapterError(str(exc).replace(key, '***')) from None
        if data.get('success') is False:
            raise AdapterError('ipqs refused the request: %s' % str(data.get('message', ''))
                               .replace(key, '***'))
        result = {'found': data.get('valid') is not False, 'marks': []}
        line_type = LINE_TYPES.get((data.get('line_type') or '').lower())
        if data.get('VOIP'):
            line_type = 'voip'
        if line_type:
            result['line_type'] = line_type
        if data.get('carrier'):
            result['carrier'] = data['carrier']
        score = data.get('fraud_score') or 0
        mark_from = int(env['ir.config_parameter'].sudo().get_param(
            MARK_FROM_PARAM, DEFAULT_MARK_FROM))
        if score >= mark_from or data.get('recent_abuse') or data.get('spammer'):
            result['marks'].append({
                'reporter': REPORTER,
                'category': 'scam' if score >= SCAM_FROM else 'spam',
                'report_count': 1,
                'external_ref': 'ipqs:' + e164,
            })
        return result
