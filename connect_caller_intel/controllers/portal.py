"""Customer portal routes for number reputation."""

from odoo import http
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal


class CallerIntelPortal(CustomerPortal):

    def _caller_intel_numbers(self):
        commercial = request.env.user.partner_id.commercial_partner_id
        return commercial._caller_intel_portal_numbers()

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if 'caller_number_count' in counters:
            values['caller_number_count'] = len(self._caller_intel_numbers())
        return values

    @http.route('/my/caller-reputation', type='http', auth='user', website=True)
    def portal_my_caller_reputation(self, **kwargs):
        values = self._prepare_portal_layout_values()
        values.update(page_name='caller_reputation', numbers=self._caller_intel_numbers())
        return request.render('connect_caller_intel.portal_my_caller_reputation', values)

    @http.route('/my/caller-reputation/<string:e164>', type='http', auth='user', website=True)
    def portal_my_caller_number(self, e164, **kwargs):
        number = next((n for n in self._caller_intel_numbers() if n['e164'] == e164), None)
        if number is None:
            raise request.not_found()
        values = self._prepare_portal_layout_values()
        values.update(page_name='caller_reputation', number=number)
        return request.render('connect_caller_intel.portal_my_caller_number', values)
