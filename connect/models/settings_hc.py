# -*- coding: utf-8 -*-
"""Harrison extras on the Connect settings singleton.

Provider-agnostic fields the NG ledger does not declare. Twilio credentials
live on connect_twilio; audio/park/voicemail fields live on connect_pbx.
"""

from odoo import fields, models


class Settings(models.Model):
    _inherit = 'connect.settings'

    record_all_calls = fields.Boolean(
        default=True,
        string='Record all calls',
        help='Record every phone call from the moment it is answered. '
             'A stop during the call keeps the rest of that call silent.',
    )
    rest_provider = fields.Selection(
        [('twilio', 'Twilio')],
        default='twilio',
        required=True,
        string='Telephony Provider',
        help='REST API provider used for calls, numbers and SIP.',
    )
    rest_api_host = fields.Char(
        string='REST API Host',
        help='Hostname of a Twilio-API-compatible provider. Leave empty to use Twilio.',
    )
    sip_domain_suffix = fields.Char(
        string='SIP Domain Suffix',
        help='Suffix for SIP domain hostnames. Leave empty for Twilio.',
    )
    default_hold_music_url = fields.Char(
        string='Default Hold Music URL',
        help='Audio URL for conference hold and call park wait music.',
    )
    webrtc_provider = fields.Selection(
        [
            ('twilio', 'Twilio WebRTC (browser phone)'),
            ('disabled', 'Disabled (SIP phones only)'),
        ],
        default='twilio',
        required=True,
        string='Browser Phone',
        help='The in-Odoo softphone uses Twilio WebRTC.',
    )
    region_auth_token = fields.Char(
        groups='base.group_erp_manager,connect.group_webhook',
        help='Regional Twilio auth token when not using us1.',
    )
    display_region_auth_token = fields.Char(
        help='Masked regional auth token shown in the form.',
    )
    openai_base_url = fields.Char(
        string='OpenAI Base URL',
        help='Custom base URL for an OpenAI-compatible API. Leave empty for default.',
    )
    recording_storage = fields.Selection(
        [
            ('twilio', 'Twilio (default)'),
            ('odoo_filestore', 'Odoo Filestore'),
        ],
        default='twilio',
        required=True,
        string='Recording Storage',
        help='Where to store call recordings and voicemails.',
    )
    delete_twilio_recording = fields.Boolean(
        default=False,
        string='Delete from Twilio After Transfer',
        help='Delete recordings from Twilio after storing them locally.',
    )
    enable_whatsapp = fields.Boolean(
        default=True,
        string='Enable WhatsApp',
        help='Show WhatsApp menus and action buttons.',
    )
    default_country_code = fields.Char(
        string='Default Country Code',
        help='ISO country code assumed when dialing numbers without a country code.',
    )
