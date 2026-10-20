from datetime import timedelta

from .base import Adapter, parse_dt

ENDPOINT = 'https://api.ftc.gov/v0/dnc-complaints'
KEY_PARAM = 'connect_caller_intel.ftc_api_key'
PAGE_SIZE = 50
MAX_PAGES_PER_RUN = 400


class FtcDnc(Adapter):
    """FTC Do Not Call reported calls.

    The API cannot filter by reported number, so this source is loaded in bulk by a daily
    cron into marks; ``lookup`` only reads what the bulk load stored.
    """

    code = 'ftc_dnc'
    batch = True

    def credential(self, env):
        return env['ir.config_parameter'].sudo().get_param(KEY_PARAM) or None

    def key_hint(self):
        return 'ir.config_parameter %s (free api.data.gov key)' % KEY_PARAM

    def lookup(self, env, e164):
        mark = env['connect.caller.mark'].sudo().search([
            ('reputation_id.e164', '=', e164),
            ('source_id.code', '=', self.code),
        ], limit=1)
        return {'found': bool(mark), 'marks': []}

    def fetch_day(self, env, day):
        """Aggregate one ``created_date`` day by reported number.

        Returns ``{e164: {'count', 'first', 'last', 'robocall'}}``.
        """
        key = self.credential(env)
        found = {}
        for page in range(MAX_PAGES_PER_RUN):
            payload = self._get(ENDPOINT, params={
                'api_key': key,
                'created_date_from': '%s 00:00:00' % day,
                'created_date_to': '%s 23:59:59' % day,
                'items_per_page': PAGE_SIZE,
                'offset': page * PAGE_SIZE,
            })
            rows = payload.get('data') or []
            for row in rows:
                attrs = row.get('attributes') or {}
                e164 = _to_e164(attrs.get('company-phone-number'))
                if not e164:
                    continue
                created = parse_dt(attrs.get('created-date'))
                entry = found.setdefault(e164, {'count': 0, 'first': created, 'last': created, 'robocall': False})
                entry['count'] += 1
                if created:
                    entry['first'] = min(filter(None, [entry['first'], created]))
                    entry['last'] = max(filter(None, [entry['last'], created]))
                entry['robocall'] = entry['robocall'] or attrs.get('recorded-message-or-robocall') == 'Y'
            if len(rows) < PAGE_SIZE:
                break
        return found

    def days_between(self, last_loaded, today):
        """Days after ``last_loaded`` up to yesterday, oldest first."""
        day = last_loaded + timedelta(days=1)
        while day < today:
            yield day
            day += timedelta(days=1)


def _to_e164(raw):
    digits = ''.join(ch for ch in str(raw or '') if ch.isdigit())
    if len(digits) == 10:
        return '+1' + digits
    if len(digits) == 11 and digits.startswith('1'):
        return '+' + digits
    return None
