# -*- coding: utf-8 -*-
from odoo import api, models


class VoiceMLCredentials(models.Model):
    _inherit = 'connect.settings'

    @api.model
    def _get_client_credentials(self):
        """VoiceML account SID and API key when VoiceML is the selected provider."""
        if self.sudo().get_param('rest_provider') == 'voiceml':
            return (
                self.sudo().get_param('voiceml_account_sid'),
                self.sudo().get_param('voiceml_api_key'),
            )
        return super()._get_client_credentials()
