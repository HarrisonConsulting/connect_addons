# -*- coding: utf-8 -*-
"""VoiceTel account on the Connect settings screen.

Stored in ir.config_parameter. The connect.settings columns stay; the
VoiceML client reads these keys first.
"""
from odoo import fields, models

from .settings import VOICETEL_DEFAULT_HOST


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    connect_voicetel_account_sid = fields.Char(
        string='VoiceTel Account SID',
        config_parameter='connect_voicetel.account_sid',
        help="Account SID from your VoiceTel account; identifies your "
             "account on the VoiceML REST API.",
    )
    connect_voicetel_api_key = fields.Char(
        string='API Key',
        config_parameter='connect_voicetel.api_key',
        help="API key from your VoiceTel account; used as the auth token "
             "for the VoiceML REST API.",
    )
    connect_voicetel_api_secret = fields.Char(
        string='API Secret',
        config_parameter='connect_voicetel.api_secret',
        help="API secret from your VoiceTel account; reserved for features "
             "that require key/secret authentication, such as the browser "
             "phone.",
    )
    connect_voicetel_rest_host = fields.Char(
        string='REST API Host',
        config_parameter='connect_voicetel.rest_host',
        default=VOICETEL_DEFAULT_HOST,
        help="Hostname of the VoiceML REST API. Leave the default unless "
             "VoiceTel support tells you otherwise.",
    )
