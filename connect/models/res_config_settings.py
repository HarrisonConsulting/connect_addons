# -*- coding: utf-8 -*-
"""Connect configuration on the standard settings screen.

Checking a provider saves and installs that module. The audio utterance
layer stays in Connect PBX; this screen only turns modules on and stores
the account switches in ir.config_parameter.
"""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    module_connect_twilio = fields.Boolean(string='Twilio')
    module_connect_voicetel = fields.Boolean(string='VoiceTel')
    module_connect_byoc = fields.Boolean(string='Bring your own carrier')
    module_connect_pbx = fields.Boolean(string='Audio, voicemail and parking')
    module_connect_elevenlabs = fields.Boolean(string='ElevenLabs transcription')
    module_connect_s3 = fields.Boolean(string='S3 recording storage')

    connect_record_all_calls = fields.Boolean(
        string='Record all calls',
        config_parameter='connect.record_all_calls',
        help='Record every call from answer. A stop during the call keeps '
             'the rest of that call silent.',
    )
    connect_transcript_calls = fields.Boolean(
        string='Transcribe calls',
        config_parameter='connect.transcript_calls',
    )
    connect_proxy_recordings = fields.Boolean(
        string='Proxy recordings',
        config_parameter='connect.proxy_recordings',
        help='Re-stream recordings using Odoo user authentication.',
    )
    connect_debug_mode = fields.Boolean(
        string='Debug logging',
        config_parameter='connect.debug_mode',
    )

    def set_values(self):
        super().set_values()
        Settings = self.env['connect.settings'].sudo()
        bridge = {
            'connect_record_all_calls': 'record_all_calls',
            'connect_transcript_calls': 'transcript_calls',
            'connect_proxy_recordings': 'proxy_recordings',
            'connect_debug_mode': 'debug_mode',
        }
        for source, target in bridge.items():
            Settings.set_param(target, self[source])
