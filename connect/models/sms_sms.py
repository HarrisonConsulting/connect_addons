import logging
from urllib.parse import urljoin

from odoo import api, fields, models

logger = logging.getLogger(__name__)


class SmsSms(models.Model):
    _inherit = 'sms.sms'

    def send(self, unlink_failed=False, unlink_sent=True, raise_exception=False):
        """Send SMS messages via Twilio using Connect credentials."""
        settings = self.env['connect.settings'].sudo()
        client = settings.get_client(region=False)
        if not client:
            self._update_sms_state_and_trackers('error', failure_type='unknown')
            return

        # Resolve from number: default outgoing caller ID
        default_number = self.env['connect.outgoing_callerid'].sudo().search(
            [('is_default', '=', True)], limit=1,
        )
        from_number = default_number.number if default_number else False
        if not from_number:
            logger.error('SMS send failed: no default outgoing caller ID configured')
            self._update_sms_state_and_trackers('error', failure_type='sms_number_missing')
            return

        api_url = settings.get_param('api_url')
        status_callback_url = urljoin(api_url, 'twilio/webhook/message_status') if api_url else False

        for sms in self:
            if sms.state != 'outgoing':
                continue
            if not sms.number:
                sms._update_sms_state_and_trackers('error', failure_type='sms_number_missing')
                continue
            try:
                create_kwargs = {
                    'to': sms.number,
                    'from_': from_number,
                    'body': sms.body or '',
                }
                if status_callback_url:
                    create_kwargs['status_callback'] = status_callback_url
                message = client.messages.create(**create_kwargs)
                if message.error_code:
                    logger.error('SMS send error to %s: [%s] %s',
                                 sms.number, message.error_code, message.error_message)
                    sms._update_sms_state_and_trackers('error', failure_type='unknown')
                else:
                    sms._update_sms_state_and_trackers('pending')
            except Exception as e:
                logger.error('SMS send failed to %s: %s', sms.number, e)
                sms._update_sms_state_and_trackers('error', failure_type='unknown')
                if raise_exception:
                    raise

        if unlink_failed:
            self.filtered(lambda s: s.state == 'error').unlink()
        if unlink_sent:
            self.filtered(lambda s: s.state in ('pending', 'sent')).unlink()