# -*- coding: utf-8 -*-
"""Harrison extras on the Connect settings singleton.

Provider-agnostic fields the NG ledger does not declare. Twilio credentials
live on connect_twilio; audio/park/voicemail fields live on connect_pbx.
"""

from odoo import api, fields, models


class Settings(models.Model):
    _inherit = 'connect.settings'

    @api.model
    def _get_client_credentials(self):
        """Let the installed provider supply its selected account credentials."""
        return False, False

    @api.model
    def _provider_log_context(self):
        """Describe the selected provider and shortened account without secrets."""
        account_sid, _token = self.sudo()._get_client_credentials()
        return 'provider=%s account=%s' % (
            self.sudo().get_param('rest_provider') or 'unconfigured',
            '%s…' % account_sid[:8] if account_sid else 'unset',
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
