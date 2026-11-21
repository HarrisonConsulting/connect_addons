# -*- coding: utf-8 -*-
from odoo import api, models


class SendSMS(models.TransientModel):
    _inherit = 'sms.composer'

    @api.model
    def _list_all_numbers(self):
        """Twilio lines, and only while Twilio is the provider that sends."""
        try:
            numbers = list(super()._list_all_numbers())
        except AttributeError:
            numbers = []
        if self.env['connect.settings']._get_message_provider() != 'twilio':
            return numbers
        seen = {number for number, _label in numbers if number}
        lines = self.env['connect.twilio.number'].sudo().search([
            ('phone_number', '!=', False),
            ('is_ignored', '=', False),
        ])
        for line in lines:
            if line.phone_number not in seen:
                numbers.append((line.phone_number, line.phone_number))
                seen.add(line.phone_number)
        return numbers

    @api.model
    def _default_outgoing_callerid(self):
        """The user's Twilio line when Twilio sends, else the other provider's."""
        parent = super()
        fallback = (
            parent._default_outgoing_callerid()
            if hasattr(parent, '_default_outgoing_callerid') else False)
        if self.env['connect.settings']._get_message_provider() != 'twilio':
            return fallback
        number = self.env.user.connect_user.twilio_outgoing_callerid.number
        return number or fallback
