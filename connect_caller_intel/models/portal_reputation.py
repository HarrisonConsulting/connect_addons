"""Portal view of the marks on a customer's numbers."""

from odoo import fields, models

from ..services import numbering
from .call_card import _safe_url

PORTAL_PARTNER_LIMIT = 500
INTERNAL_SOURCE_CODES = ('staff', 'local_partner')


class ResPartner(models.Model):
    _inherit = 'res.partner'

    def _caller_intel_portal_numbers(self):
        """Return the external marks on this commercial partner's numbers.

        The scope is the partner and its descendants, read with sudo. Staff and
        local partner marks, evidence links, captures and identities never leave
        this method.
        """
        self.ensure_one()
        partners = self.sudo().search(
            [('id', 'child_of', self.id)], limit=PORTAL_PARTNER_LIMIT)
        company_country = self.env.company.country_id.code or 'US'
        contacts = {}
        for partner in partners:
            country = partner.country_id.code or company_country
            for raw in (partner.phone, partner.mobile):
                e164 = numbering.analyze(raw, country)['e164'] if raw else ''
                if e164:
                    names = contacts.setdefault(e164, [])
                    if partner.display_name not in names:
                        names.append(partner.display_name)
        if not contacts:
            return []
        Mark = self.env['connect.caller.mark'].sudo()
        marks = Mark.search([
            ('reputation_id.e164', 'in', list(contacts)),
            ('state', '=', 'active'),
            ('source_id.code', 'not in', INTERNAL_SOURCE_CODES),
        ])
        category_labels = dict(Mark._fields['category']._description_selection(self.env))
        today = fields.Date.context_today(self)
        by_number = {}
        for mark in marks:
            reported = mark.last_reported or mark.first_reported or mark.create_date
            remediation = mark.remediation_id
            by_number.setdefault(mark.reputation_id.e164, []).append({
                'reporter': mark.reporter,
                'category': category_labels.get(mark.category, ''),
                'report_count': mark.report_count,
                'first_reported': fields.Date.to_date(mark.first_reported) if mark.first_reported else False,
                'last_reported': fields.Date.to_date(mark.last_reported) if mark.last_reported else False,
                'age_days': max((today - fields.Date.to_date(reported)).days, 0),
                'clearing_url': _safe_url(mark.clearing_url or remediation.dispute_url),
                'steps': remediation.steps or mark.source_id.clearing_note or '',
                'removable': remediation.removable if remediation else True,
                'remediation_name': remediation.name or '',
            })
        numbers = [
            {'e164': e164, 'contacts': contacts[e164], 'marks': found}
            for e164, found in by_number.items()
        ]
        numbers.sort(key=lambda number: (-len(number['marks']), number['e164']))
        return numbers
