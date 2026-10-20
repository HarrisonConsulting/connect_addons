# -*- coding: utf-8 -*-
import logging

from odoo import models, api, SUPERUSER_ID
from odoo.exceptions import AccessError, ValidationError

logger = logging.getLogger(__name__)


class ConnectMessage(models.Model):
    _inherit = 'connect.message'

    @api.model
    def _own_numbers(self):
        """VoiceML numbers this database sends from. Core's
        _compute_direction asks every provider through this chain."""
        return super()._own_numbers() | set(
            self.env['connect.voiceml.number'].search([]).mapped('phone_number'))

    # connect_twilio defines the unsuffixed receive() on this shared
    # model without super(), so Odoo would run only whichever module merged
    # last for both providers' webhooks. VoiceML's is _receive_voiceml().

    @api.model
    def _receive_voiceml(self, params):
        # SMS-only for the scaffold; WhatsApp is not yet modelled on VoiceML.
        try:
            if params.get('SmsStatus') == 'received':
                from_number = params.get('From')
                to_number = params.get('To')
                sid = params.get('MessageSid')
                if sid and self.env['connect.message'].sudo().search_count(
                        [('message_sid', '=', sid)]):
                    # VoiceML retries webhooks; a replayed sid is already stored.
                    return 'OK'
                values = self.get_receive_message_values(params)
                partner = self.env['res.partner'].get_partner_by_number(from_number)
                if partner:
                    values.update({'partner': partner.id})
                self.env['connect.message'].sudo().create(values)
            else:
                message = self.env['connect.message'].sudo().search(
                    [('message_sid', '=', params.get('MessageSid'))])
                if message:
                    message.update({'status': params.get('SmsStatus')})
                    if params.get('SmsStatus') == 'failed':
                        message.update({
                            'error_code': params.get('ErrorCode'),
                            'error_message': params.get('ErrorMessage'),
                            'has_error': True,
                        })
        except Exception:
            # Re-raise so the controller answers 500 and VoiceML retries.
            logger.exception('Error handling incoming SMS')
            raise
        return 'OK'

    def send(self, recipient, body, res_id=None, res_model=None,
             outgoing_callerid=None, **kwargs):
        if self.env['connect.settings']._get_message_provider() != 'voiceml':
            return super().send(
                recipient, body, res_id=res_id, res_model=res_model,
                outgoing_callerid=outgoing_callerid, **kwargs)
        sender_user = self.env.user
        if res_model and res_id:
            if res_model not in self.env:
                raise ValidationError('Unknown model %s.' % res_model)
            # As the calling user, before any sudo.
            self.env[res_model].browse(res_id).check_access('read')
        own = sender_user.connect_user.voiceml_outgoing_callerid
        if outgoing_callerid:
            allowed = set(own.mapped('number')) | set(
                self.env['connect.voiceml.outgoing_callerid'].sudo().search(
                    [('is_default', '=', True)]).mapped('number'))
            if outgoing_callerid not in allowed:
                raise AccessError('You may not send from %s.' % outgoing_callerid)
            sender = outgoing_callerid
        else:
            number = sender_user.connect_user.voiceml_outgoing_callerid
            if not number:
                raise ValidationError('You dont have an outgoing callerid number!')
            sender = number.number
        client = self.env['connect.settings']._voiceml_client()
        message = client.messages.create(to=recipient, from_=sender, body=body)
        if getattr(message, 'error_code', None):
            raise ValidationError('Unexpected error! Contact admin or maintainer!')
        partner = self.env['res.partner'].get_partner_by_number(recipient)
        self.env['connect.message'].sudo().create({
            'message_sid': message.sid,
            'from_number': sender,
            'to_number': recipient,
            'body': body,
            'sender_user': sender_user.id,
            'res_id': res_id,
            'res_model': res_model,
            'status': 'sent',
            'message_type': 'sms',
            'partner': partner.id if partner else False,
        })
        if res_model and res_id:
            obj = self.env[res_model].with_user(SUPERUSER_ID).browse(res_id)
            if hasattr(obj, 'message_post'):
                obj.with_context(mail_create_nosubscribe=True).message_post(
                    body=body, subtype_id=self.env.ref('mail.mt_note').id)
