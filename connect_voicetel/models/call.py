# -*- coding: utf-8 -*-
import json
import logging

from odoo import fields, models, api

from odoo.addons.connect.models.settings import debug

from .voicetel_response import VoiceResponse

logger = logging.getLogger(__name__)

IGNORE_ERROR_CODES = ['32009']


class VoicetelCall(models.Model):
    _inherit = 'connect.call'

    call_sid = fields.Char(
        string='Provider Call SID', readonly=True,
        help='SID of this call at whichever provider handled it '
             '(Twilio, VoiceTel, ...).')

    # Suffixed _voicetel: connect_twilio defines on_call_action/
    # on_call_status/on_vm_recording_status on this same shared model too,
    # neither chained via super(), so an unnamespaced name here would let
    # whichever module's class the ORM merges last fully replace the
    # other's. Our own /voicetel/webhook/* controller calls these by name,
    # so nothing else ever needs to resolve them.

    @api.model
    def on_call_action_voicetel(self, params):
        debug(self, 'On call action: %s' % params)
        response = VoiceResponse()
        response.hangup()
        return response.to_xml()

    @api.model
    def on_call_status_voicetel(self, params):
        """VoiceTel webhook adapter: map params, delegate to core."""
        self = self.sudo()
        channel = self.env['connect.channel'].on_call_status_voicetel(params)
        if not channel:
            logger.error('No channel returned from on_call_status_voicetel!')
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
                    title='Call Error',
                    message=params.get('ErrorMessage', ''),
                    warning=True,
                )
        return call_id

    @api.model
    def on_vm_recording_status_voicetel(self, params):
        debug(self.sudo(), 'On voicemail recording status: %s' % json.dumps(params, indent=2))
        channel = self.sudo().env['connect.channel'].search(
            [('sid', '=', params['CallSid'])])
        if channel and channel.call:
            channel.call.write({
                'voicemail_url': params.get('RecordingUrl'),
                'voicemail_duration': int(params.get('RecordingDuration', 0)),
            })
        return True
