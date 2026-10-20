"""Ring card payload for the softphone."""

from odoo import api, fields, models

from ..services import heuristics, numbering

CARD_SIGNAL_LIMIT = 5


def _safe_url(url):
    """Return ``url`` only when it is a plain web link, else an empty string."""
    url = (url or '').strip()
    return url if url.lower().startswith(('https://', 'http://')) else ''


class ConnectCall(models.Model):
    _inherit = 'connect.call'

    @api.model
    def caller_intel_card(self, number):
        """Return what the ring card shows for ``number``, read from stored rows only.

        Every string is plain text: the caller name is network supplied and
        the client renders it with t-esc, never as markup.
        """
        facts = numbering.analyze(number or '')
        e164 = facts['e164'] or (number or '')
        card = {
            'e164': e164, 'verdict': 'unknown', 'risk_score': 0.0, 'name': '',
            'partner': False, 'commercial_partner': False, 'attestation': False,
            'line_type': facts['line_type'], 'region': facts['region'],
            'marks': [], 'signals': [],
        }
        partner = self.env['res.partner'].get_partner_by_number(number) if number else False
        if partner:
            commercial = partner.commercial_partner_id
            card['partner'] = {'id': partner.id, 'name': partner.display_name}
            card['commercial_partner'] = {'id': commercial.id, 'name': commercial.display_name}
        rep = self.env['connect.caller.reputation'].search([('e164', '=', e164)], limit=1)
        if rep:
            card.update(self._caller_intel_reputation_card(rep))
        card['verdict'] = heuristics.verdict(card['signals'], bool(partner))
        return card

    @api.model
    def _caller_intel_reputation_card(self, rep):
        marks = rep.mark_ids.filtered(lambda m: m.state == 'active')
        signals = self.env['connect.caller.signal'].search(
            [('reputation_id', '=', rep.id), ('state', '!=', 'dismissed')],
            order='id desc', limit=CARD_SIGNAL_LIMIT)
        category_labels = dict(self.env['connect.caller.mark']._fields['category']._description_selection(self.env))
        code_labels = dict(self.env['connect.caller.signal']._fields['code']._description_selection(self.env))
        return {
            'risk_score': rep.spam_score,
            'name': rep.cnam_last or rep.candidate_business_name or '',
            'attestation': rep.last_attestation or False,
            'line_type': rep.line_type,
            'region': rep.region or '',
            'marks': [{
                'reporter': mark.reporter,
                'category': category_labels.get(mark.category, ''),
                'report_count': mark.report_count,
                'last_reported': fields.Datetime.to_string(mark.last_reported) if mark.last_reported else '',
                'clearing_url': _safe_url(mark.clearing_url),
                'evidence_url': _safe_url(mark.evidence_url),
            } for mark in marks],
            'signals': [{
                'code': signal.code,
                'label': code_labels.get(signal.code, ''),
                'severity': signal.severity,
                'reason': signal.reason or '',
            } for signal in signals],
        }
