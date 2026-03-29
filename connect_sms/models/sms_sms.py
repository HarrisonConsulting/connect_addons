import logging

from odoo import models
from odoo.addons.sms_twilio.tools.sms_twilio import get_twilio_from_number

_logger = logging.getLogger(__name__)

# Map sms_twilio result states to connect.message statuses
_RESULT_STATE_TO_CONNECT_STATUS = {
    'sent': 'sent',
    'success': 'sent',
    'processing': 'queued',
    'pending': 'sent',
    'delivered': 'delivered',
    'server_error': 'failed',
}


class SmsSms(models.Model):
    _inherit = 'sms.sms'

    def _handle_call_result_hook(self, results):
        """After standard send, create connect.message records for journaling.

        This preserves connect.message's value (message history UI, chatter
        links, status tracking) without requiring connect to override the
        entire send pipeline.
        """
        super()._handle_call_result_hook(results)
        self._create_connect_messages(results)

    def _create_connect_messages(self, results):
        """Create connect.message records from SMS send results."""
        results_by_uuid = {r['uuid']: r for r in results}
        ConnectMessage = self.env['connect.message'].sudo()

        for sms in self:
            result = results_by_uuid.get(sms.uuid)
            if not result:
                continue

            sid = result.get('sms_twilio_sid')
            if not sid:
                # No Twilio SID means send failed before reaching Twilio
                continue

            # Avoid duplicates
            if ConnectMessage.search_count([('message_sid', '=', sid)], limit=1):
                continue

            # Resolve from-number using sms_twilio's number selection
            from_number = ''
            company = sms._get_sms_company()
            if company.sms_twilio_number_ids:
                from_number_rec = get_twilio_from_number(company.sudo(), sms.number)
                if from_number_rec:
                    from_number = from_number_rec.number
            if not from_number:
                default_callerid = self.env['connect.outgoing_callerid'].sudo().search(
                    [('is_default', '=', True)], limit=1,
                )
                from_number = default_callerid.number if default_callerid else ''

            # Resolve linked record from mail.message
            res_model = False
            res_id = False
            if sms.mail_message_id:
                res_model = sms.mail_message_id.model
                res_id = sms.mail_message_id.res_id

            state = result.get('state', 'unknown')
            connect_status = _RESULT_STATE_TO_CONNECT_STATUS.get(state, 'queued')

            ConnectMessage.create({
                'message_sid': sid,
                'from_number': from_number,
                'to_number': sms.number,
                'body': sms.body,
                'status': connect_status,
                'sender_user': self.env.uid,
                'partner': sms.partner_id.id if sms.partner_id else False,
                'res_model': res_model,
                'res_id': res_id,
                'message_type': 'sms',
            })
