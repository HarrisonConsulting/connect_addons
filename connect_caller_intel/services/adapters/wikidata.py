import csv
import io
import re

import phonenumbers

from ..heuristics import IDENTITY_CONFIDENCE
from .base import Adapter, polite_user_agent

SPARQL_ENDPOINT = 'https://query.wikidata.org/sparql'
ENTITY_ENDPOINT = 'https://www.wikidata.org/w/api.php'
BULK_QUERY = 'SELECT ?item ?tel WHERE { ?item wdt:P1329 ?tel }'
MAX_IDENTITIES = 3
INSERT_PAGE = 5000
QID = re.compile(r'Q\d+$')


def to_e164(raw, default_region=None):
    """E.164 form of a stored phone value, or None when it is not a valid number.

    A value without a leading ``+`` needs ``default_region`` to be read.
    """
    text = (raw or '').strip()
    if not text.startswith('+') and not default_region:
        return None
    try:
        num = phonenumbers.parse(text, default_region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(num):
        return None
    return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)


def skip_for_privacy(env, rep):
    """True for numbers a public registry is never asked about: mobiles and known contacts."""
    return rep.line_type == 'mobile' or bool(
        env['res.partner'].get_partner_by_number(rep.e164))


class WikidataPhone(Adapter):
    """Businesses that Wikidata lists under a phone number (property P1329).

    A weekly bulk import fills ``connect.caller.wikidata``; a call is matched locally. Only the
    label of a matched item is fetched from Wikidata, once, and stored.
    """

    code = 'wikidata'
    nanp_only = False

    def applies_to(self, env, rep):
        return super().applies_to(env, rep) and not skip_for_privacy(env, rep)

    def credential(self, env):
        return bool(env['connect.caller.wikidata'].sudo().search_count([], limit=1))

    def key_hint(self):
        return 'a loaded connect.caller.wikidata table (weekly import cron)'

    def user_agent(self, env):
        return {'User-Agent': polite_user_agent(env)}

    def lookup(self, env, e164):
        rows = env['connect.caller.wikidata'].sudo().search(
            [('e164', '=', e164)], order='qid', limit=MAX_IDENTITIES)
        if not rows:
            return {'found': False, 'identities': []}
        unlabelled = rows.filtered(lambda r: not r.label)
        if unlabelled:
            self._fetch_labels(env, unlabelled)
        identities = [{
            'name': row.label, 'kind': 'business',
            'url': 'https://www.wikidata.org/wiki/%s' % row.qid,
            'external_ref': row.qid,
            'confidence': IDENTITY_CONFIDENCE['wikidata'],
        } for row in rows if row.label]
        return {'found': bool(identities), 'identities': identities}

    def _fetch_labels(self, env, rows):
        env['connect.caller.source']._caller_intel_throttle(self.code, 1.0)
        payload = self._get(ENTITY_ENDPOINT, headers=self.user_agent(env), params={
            'action': 'wbgetentities', 'ids': '|'.join(sorted(set(rows.mapped('qid')))),
            'props': 'labels', 'languages': 'en', 'format': 'json',
        })
        entities = payload.get('entities') or {}
        for row in rows:
            label = ((entities.get(row.qid) or {}).get('labels') or {}).get('en', {}).get('value')
            if label:
                row.label = label

    def import_bulk(self, env):
        """Replace ``connect.caller.wikidata`` with the current P1329 export. Returns the row count."""
        env['connect.caller.source']._caller_intel_throttle(self.code, 1.0)
        text = self._get_text(
            SPARQL_ENDPOINT, params={'query': BULK_QUERY},
            headers=dict(self.user_agent(env), Accept='text/csv'))
        pairs = {}
        for row in csv.DictReader(io.StringIO(text)):
            qid = (row.get('item') or '').rsplit('/', 1)[-1]
            e164 = to_e164(row.get('tel'))
            if e164 and QID.match(qid):
                pairs[(e164, qid)] = None
        if not pairs:
            return 0
        Model = env['connect.caller.wikidata'].sudo()
        labels = {(r.e164, r.qid): r.label for r in Model.search([('label', '!=', False)])}
        cr = env.cr
        cr.execute('DELETE FROM connect_caller_wikidata')
        keys = sorted(pairs)
        for start in range(0, len(keys), INSERT_PAGE):
            cr.execute_values(
                'INSERT INTO connect_caller_wikidata (e164, qid, label) VALUES %s',
                [(e164, qid, labels.get((e164, qid))) for e164, qid in keys[start:start + INSERT_PAGE]])
        Model.invalidate_model()
        return len(keys)
