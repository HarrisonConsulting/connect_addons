# -*- coding: utf-8 -*-

from odoo import api, models, fields


class SendSMS(models.TransientModel):
    _inherit = 'sms.composer'

    outgoing_callerid = fields.Selection(selection='_list_all_numbers')

    @api.model
    def _list_all_numbers(self):
        self._cr.execute("SELECT phone_number, COALESCE(phone_number, phone_number) FROM connect_number ORDER BY 2")
        return self._cr.fetchall()
