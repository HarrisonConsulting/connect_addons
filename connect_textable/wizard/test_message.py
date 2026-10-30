from odoo import fields, models
from odoo.exceptions import AccessError


class TextableTestMessage(models.TransientModel):
    _name = 'connect.textable.test.message'
    _description = 'Textable Test Message'

    number_id = fields.Many2one(
        'connect.textable.number', required=True, ondelete='cascade',
        domain=[('active', '=', True)],
        help='The number to send from. Its campaign must be approved.')
    recipient = fields.Char(
        required=True,
        help='A handset you control, in E.164 form, for example +13125550123.')
    body = fields.Text(
        required=True, default='Odoo Connect test message.',
        help='The text to send. It goes through the same gate as any other send.')

    def action_send(self):
        """One send through the gated path, as a system administrator who
        need not be assigned to the number."""
        self.ensure_one()
        if not self.env.is_system():
            raise AccessError('Only system administrators can send a Textable test message.')
        message = self.env['connect.message']._textable_send(
            self.recipient, self.body, sender_number=self.number_id,
            allow_unassigned=True)
        ok = message.textable_state == 'sent_unconfirmed'
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Textable test message',
                'message': '%s%s' % (
                    dict(message._fields['textable_state'].selection).get(
                        message.textable_state, ''),
                    '' if ok else ': %s' % (message.error_message or ''),
                ),
                'type': 'success' if ok else 'warning',
                'sticky': not ok,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
