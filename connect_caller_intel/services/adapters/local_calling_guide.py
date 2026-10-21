from datetime import timedelta

from lxml import etree

from odoo import fields

from .base import Adapter, AdapterError, polite_user_agent

ENDPOINT = 'https://localcallingguide.com/xmlprefix.php'
CACHE_DAYS = 30
MIN_INTERVAL = 1.0
PARSER = etree.XMLParser(resolve_entities=False, no_network=True)


def split_nanp(e164):
    """Area code and exchange of a +1 number, or None."""
    digits = e164.lstrip('+')
    if len(digits) == 11 and digits.startswith('1'):
        return digits[1:4], digits[4:7]
    return None


def parse_prefixes(body):
    """Whole-exchange facts from an xmlprefix response; empty when the prefix is unknown."""
    try:
        root = etree.fromstring(body.encode('utf-8'), PARSER)
    except etree.XMLSyntaxError as exc:
        raise AdapterError('Local Calling Guide returned unreadable XML') from exc
    blocks = root.findall('prefixdata')
    if not blocks:
        return {}
    whole = next((b for b in blocks if (b.findtext('x') or '').strip() == 'A'), blocks[0])

    def text(tag):
        return (whole.findtext(tag) or '').strip()

    return {
        'locality': text('rc'), 'state_code': text('region'), 'ocn': text('ocn'),
        'company': text('company-name'), 'lata': text('lata'), 'pooled': len(blocks) > 1,
    }


class LocalCallingGuide(Adapter):
    """Fallback for exchanges NANPA does not list. Answers come from the cached exchange table."""

    code = 'local_calling_guide'
    nanp_only = True

    def applies_to(self, env, rep):
        return (super().applies_to(env, rep)
                and rep.nxx_status == 'unknown' and not rep.rate_center)

    def lookup(self, env, e164):
        parts = split_nanp(e164)
        if not parts:
            return {'found': False, 'marks': []}
        npa_nxx = ''.join(parts)
        Nxx = env['connect.caller.nxx'].sudo()
        row = Nxx.search([('npa_nxx', '=', npa_nxx)], limit=1)
        fresh_after = fields.Datetime.now() - timedelta(days=CACHE_DAYS)
        if not (row and row.lcg_checked_at and row.lcg_checked_at > fresh_after):
            env['connect.caller.source']._caller_intel_throttle(self.code, MIN_INTERVAL)
            body = self._get_text(
                ENDPOINT, params={'npa': parts[0], 'nxx': parts[1]},
                headers={'User-Agent': polite_user_agent(env)})
            facts = parse_prefixes(body)
            if not facts:
                return {'found': False, 'marks': []}
            row = self._store(Nxx, row, npa_nxx, facts)
        return {'found': True, 'marks': [], 'numbering': {
            'rate_center': row.locality or row.rate_center,
            'numbering_state': row.state_code,
            'ocn': row.ocn,
            'ocn_company': row.company,
            'nxx_status': row.status,
        }}

    def _store(self, Nxx, row, npa_nxx, facts):
        """Cache the guide's answer on the exchange row, creating an ``lcg`` row when NANPA has none."""
        checked = {'locality': facts['locality'], 'lata': facts['lata'],
                   'lcg_checked_at': fields.Datetime.now()}
        if row:
            row.write(dict(
                checked,
                **{key: facts[key] for key in ('state_code', 'ocn', 'company') if not row[key]}))
            return row
        return Nxx.create(dict(
            checked, npa_nxx=npa_nxx, npa=npa_nxx[:3], nxx=npa_nxx[3:], origin='lcg',
            status='assigned', in_service=True, pooled=facts['pooled'],
            state_code=facts['state_code'], ocn=facts['ocn'], company=facts['company']))
