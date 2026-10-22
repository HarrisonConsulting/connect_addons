# -*- coding: utf-8 -*-
from odoo import api, fields, models


class VoiceTelCredentials(models.Model):
    _inherit = 'connect.settings'

    rest_provider = fields.Selection(
        selection_add=[('voicetel', 'VoiceTel')],
        ondelete={'voicetel': 'set null'},
    )

    @api.model
    def _get_client_credentials(self):
        """VoiceTel account SID and API key when VoiceTel is the selected provider."""
        if self.sudo().get_param('rest_provider') == 'voicetel':
            return (
                self.sudo().get_param('voicetel_account_sid'),
                self.sudo().get_param('voicetel_api_key'),
            )
        return super()._get_client_credentials()
