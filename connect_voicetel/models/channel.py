# -*- coding: utf-8 -*-
import json
import logging

from odoo import models, api

from odoo.addons.connect.models.settings import debug
from .settings import MAX_EXTEN_LEN

logger = logging.getLogger(__name__)


class VoicetelChannel(models.Model):
    _inherit = 'connect.channel'

    # _voicetel suffix: see the note on connect.call above — connect_twilio
    # overrides on_call_status on this same shared model without a super()
    # call, so an unnamespaced name here would let whichever module loads
    # last silently take over the other's call-status webhook.

    @api.model
    def on_call_status_voicetel(self, params):
        """VoiceTel webhook adapter: map params and delegate to core."""
        debug(self, 'On channel status: %s' % json.dumps(params, indent=2))
        generic = self._map_voicetel_params(params)
        return self.process_channel_event(generic)

    def _strip_voicetel_exten_plus(self, number):
        if not isinstance(number, str) or not number.startswith('+'):
            return number
        candidate = number[1:]
        if not candidate.isdigit() or len(candidate) > MAX_EXTEN_LEN:
            return number
        exten = self.env['connect.voicetel.exten'].sudo().search(
            [('number', '=', candidate)], limit=1)
        return candidate if exten else number

    def _map_voicetel_params(self, params):
        return {
            'sid': params['CallSid'],
            'caller': self._strip_voicetel_exten_plus(params.get('Caller')),
            'called': self._strip_voicetel_exten_plus(params.get('Called')),
            'to': params.get('To'),
            'technical_direction': params.get('Direction'),
            'status': params.get('CallStatus'),
            'duration': int(params.get('CallDuration', 0)),
            'call_type': 'phone',
            'parent_sid': params.get('ParentCallSid'),
        }
