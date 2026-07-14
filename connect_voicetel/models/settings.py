# -*- coding: utf-8 -*-

from odoo import api, fields, models

VOICETEL_DEFAULT_HOST = 'voiceml.voicetel.com'


class VoicetelSettings(models.Model):
    _inherit = 'connect.settings'

    rest_provider = fields.Selection(
        selection_add=[('voicetel', 'VoiceTel')],
        ondelete={'voicetel': 'set default'},
    )
    voicetel_account_sid = fields.Char(
        help="Account SID from your VoiceTel account; identifies your "
             "account on the VoiceML REST API.")
    voicetel_api_key = fields.Char(
        groups="base.group_erp_manager,connect.group_connect_webhook",
        help="API key from your VoiceTel account; used as the auth token "
             "for the VoiceML REST API.")
    display_voicetel_api_key = fields.Char()
    voicetel_api_secret = fields.Char(
        groups="base.group_erp_manager",
        help="API secret from your VoiceTel account; reserved for features "
             "that require key/secret authentication, such as the browser "
             "phone.")
    display_voicetel_api_secret = fields.Char()
    voicetel_rest_host = fields.Char(
        default=VOICETEL_DEFAULT_HOST,
        help="Hostname of the VoiceML REST API. Leave the default unless "
             "VoiceTel support tells you otherwise.")

    @api.model
    def _get_protected_fields(self):
        return super()._get_protected_fields() + [
            'display_voicetel_api_key',
            'display_voicetel_api_secret',
        ]

    @api.model
    def _get_client_credentials(self):
        if self.sudo().get_param('rest_provider') == 'voicetel':
            return (
                self.sudo().get_param('voicetel_account_sid'),
                self.sudo().get_param('voicetel_api_key'),
            )
        return super()._get_client_credentials()

    @api.model
    def _get_rest_api_host(self):
        if self.sudo().get_param('rest_provider') == 'voicetel':
            host = (self.sudo().get_param('voicetel_rest_host') or '').strip()
            return host or VOICETEL_DEFAULT_HOST
        return super()._get_rest_api_host()
