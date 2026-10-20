# -*- coding: utf-8 -*-
import json
import logging
from urllib.parse import urlparse

from odoo import models, api

from odoo.addons.connect.models.settings import debug

logger = logging.getLogger(__name__)

IGNORE_ERROR_CODES = ['32009']


class Call(models.Model):
    _inherit = 'connect.call'

    # connect_twilio defines the unsuffixed handler names on this shared
    # model without super(), so Odoo would run only whichever module merged
    # last for both providers' webhooks. VoiceML's handlers carry _voiceml.

    @api.model
    def _on_call_action_voiceml(self, params):
        from .twiml_builder import TwiMLResponse
        debug(self, 'On call action: %s' % params)
        response = TwiMLResponse()
        response.hangup()
        return response.to_string()

    @api.model
    def _on_call_status_voiceml(self, params):
        """VoiceML webhook adapter: map params, delegate to core."""
        self = self.sudo()
        channel = self.env['connect.channel']._on_call_status_voiceml(params)
        if not channel:
            logger.error('No channel returned from on_call_status!')
            return False

        error_data = None
        if (params.get('ErrorCode')
                and params.get('ErrorCode') not in IGNORE_ERROR_CODES):
            error_data = {
                'error_code': params.get('ErrorCode'),
                'error_message': params.get('ErrorMessage'),
            }

        call_id = self.process_call_event(channel, error_data)

        if error_data and channel.call and channel.call.direction == 'outgoing':
            user = channel.caller_user or channel.call.caller_user
            if user:
                self.env['connect.settings'].connect_notify(
                    notify_uid=user.id,
                    title="Call Error",
                    message=params.get('ErrorMessage', ''),
                    warning=True,
                )
        return call_id

    @api.model
    def _on_vm_recording_status_voiceml(self, params):
        debug(self.sudo(), 'On voicemail recording status: %s' % json.dumps(params, indent=2))
        channel = self.sudo().env['connect.channel'].search(
            [('sid', '=', params['CallSid'])])
        recording_url = params.get('RecordingUrl') or ''
        parsed = urlparse(recording_url)
        host = (parsed.hostname or '').lower()
        if recording_url and (
                parsed.scheme != 'https'
                or host != self.env['connect.settings']._voiceml_api_host()):
            # Core's /connect/voicemail proxy GETs this URL server-side, so a
            # foreign host here would be a read-anything SSRF.
            logger.warning('Ignoring voicemail RecordingUrl on foreign or non-https host %s', host)
            return False
        if channel and channel.call:
            vals = {
                'voicemail_url': recording_url,
                'voicemail_duration': int(params.get('RecordingDuration') or 0),
            }
            if 'voicemail_sid' in channel.call._fields and params.get('RecordingSid'):
                vals['voicemail_sid'] = params['RecordingSid']
            channel.call.write(vals)
        return True
