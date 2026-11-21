# -*- coding: utf-8 -*-
import re

from odoo import api, fields, models, release


class SendSMS(models.TransientModel):
    _inherit = 'sms.composer'

    outgoing_callerid = fields.Selection(
        selection='_list_all_numbers',
        string='From',
        help='The line this message is sent from.',
    )

    @api.model
    def _list_all_numbers(self):
        """Lines the provider that will send this message can use.

        A messaging module extends this and adds its own lines only while
        it is that provider.
        """
        return []

    @api.model
    def _default_outgoing_callerid(self):
        """The user's line on the provider that will send, or False."""
        return False

    @api.model
    def _allowed_outgoing_callerids(self):
        return {number for number, _label in self._list_all_numbers() if number}

    def _outgoing_callerid_to_send(self):
        """A From value the active provider can send from, or False."""
        self.ensure_one()
        allowed = self._allowed_outgoing_callerids()
        if self.outgoing_callerid in allowed:
            return self.outgoing_callerid
        fallback = self._default_outgoing_callerid()
        if fallback in allowed:
            return fallback
        return False

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if 'outgoing_callerid' not in fields_list:
            return values
        allowed = self._allowed_outgoing_callerids()
        chosen = values.get('outgoing_callerid')
        if chosen not in allowed:
            fallback = self._default_outgoing_callerid()
            values['outgoing_callerid'] = fallback if fallback in allowed else False
        return values

    def _action_send_sms(self):
        number = self.recipient_single_number or self.recipient_single_number_itf
        if release.version_info[0] < 17:
            number = re.sub(r"[^\d+]+", "", number)
        else:
            number = self._phone_format(number=number)
        self.env['connect.message'].send(
            number, self.body, self.res_id, self.res_model,
            self._outgoing_callerid_to_send())
