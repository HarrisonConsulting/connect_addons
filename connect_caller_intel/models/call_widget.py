"""Softphone call widget fields and ring card actions."""

from datetime import timedelta

from odoo import api, fields, models

RECENT_CALL_WINDOW = timedelta(hours=2)


class ConnectCall(models.Model):
    _inherit = 'connect.call'

    def get_widget_fields(self):
        return super().get_widget_fields() + ['caller_verdict']

    @api.model
    def caller_intel_card(self, number):
        """Add the ring call and the staff marking right to the card."""
        card = super().caller_intel_card(number)
        call = self.search([
            ('direction', '=', 'incoming'),
            ('caller_reputation_id.e164', '=', card['e164']),
            ('create_date', '>=', fields.Datetime.now() - RECENT_CALL_WINDOW),
        ], order='id desc', limit=1)
        card['call_id'] = call.id or False
        card['can_mark'] = self.env.user.has_group('connect.group_user')
        return card
