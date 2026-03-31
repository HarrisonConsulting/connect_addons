# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ConversationWizard(models.TransientModel):
    _name = 'connect.conversation_wizard'
    _description = 'New Conversation'

    partner_id = fields.Many2one('res.partner', string='Contact')
    phone = fields.Char(string='Phone Number', required=True)
    channel_type = fields.Selection([
        ('sms', 'SMS'),
        ('whatsapp', 'WhatsApp'),
    ], required=True, default='sms')
    phone_a = fields.Char(string='Our Number', readonly=True)

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        # Default our number from user's outgoing callerid
        try:
            user = self.env.user
            if user.connect_user and user.connect_user.outgoing_callerid:
                vals['phone_a'] = user.connect_user.outgoing_callerid.number
        except Exception:
            pass
        return vals

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        if self.partner_id:
            phone = (
                self.partner_id.phone_sanitized
                or self.partner_id.mobile
                or self.partner_id.phone
            )
            if phone:
                self.phone = phone

    @api.onchange('channel_type')
    def _onchange_channel_type(self):
        if self.channel_type == 'whatsapp':
            try:
                sender = self.env['connect.whatsapp_sender'].get_default_sender()
                if sender:
                    self.phone_a = sender.number
            except Exception:
                pass
        elif self.channel_type == 'sms':
            try:
                user = self.env.user
                if user.connect_user and user.connect_user.outgoing_callerid:
                    self.phone_a = user.connect_user.outgoing_callerid.number
            except Exception:
                pass

    def action_start_conversation(self):
        """Find or create conversation and open it."""
        self.ensure_one()
        if not self.phone:
            raise ValidationError('Phone number is required.')
        if not self.phone_a:
            raise ValidationError('No outgoing number configured. Check your Connect user settings.')

        conv = self.env['connect.conversation'].get_or_create(
            channel_type=self.channel_type,
            phone_a=self.phone_a,
            phone_b=self.phone,
            partner=self.partner_id or None,
        )
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'connect.conversation',
            'res_id': conv.id,
            'view_mode': 'form',
            'target': 'current',
        }
