import logging

from odoo import models

_logger = logging.getLogger(__name__)

# Map Odoo SMS states to connect.message statuses
_SMS_STATE_TO_CONNECT_STATUS = {
    'outgoing': 'queued',
    'process': 'sending',
    'pending': 'sent',
    'sent': 'delivered',
    'error': 'failed',
    'canceled': 'canceled',
}


class SmsTracker(models.Model):
    _inherit = 'sms.tracker'

    def _action_update_from_sms_state(self, sms_state, failure_type=False, failure_reason=False):
        """Extend to sync delivery status to connect.message records."""
        res = super()._action_update_from_sms_state(
            sms_state, failure_type=failure_type, failure_reason=failure_reason,
        )
        self._sync_connect_message_status(sms_state, failure_reason=failure_reason)
        return res

    def _sync_connect_message_status(self, sms_state, failure_reason=False):
        """Update connect.message records matching our Twilio SID."""
        connect_status = _SMS_STATE_TO_CONNECT_STATUS.get(sms_state)
        if not connect_status:
            return

        ConnectMessage = self.env['connect.message'].sudo()
        for tracker in self.filtered('sms_twilio_sid'):
            msg = ConnectMessage.search(
                [('message_sid', '=', tracker.sms_twilio_sid)], limit=1,
            )
            if msg and msg.status != connect_status:
                vals = {'status': connect_status}
                if sms_state == 'error' and failure_reason:
                    vals['has_error'] = True
                    vals['error_message'] = failure_reason
                msg.write(vals)
