# -*- coding: utf-8 -*-
from odoo import models


class VoicetelDomain(models.Model):
    _inherit = 'connect.domain'

    def _credential_is_person(self, credential):
        """Exclude per-browser-tab SIP credentials from the user import.

        The VoiceTel browser phone mints one real SIP credential per browser
        tab into the SAME domain credential list the person credentials live
        in (the only list the provider authenticates registrations against).
        Those are session artifacts tracked in connect.user_browser_credential,
        not people; importing one creates a phantom connect.user.
        """
        if not super()._credential_is_person(credential):
            return False
        tracked = self.env['connect.user_browser_credential'].sudo().search_count(
            [('sid', '=', credential.sid)])
        if tracked:
            return False
        # Tab credentials minted by OTHER Odoo instances sharing this
        # provider account (e.g. another environment) are not in our
        # tracking table — fall back to the minting naming convention
        # (see VoicetelUser._get_voicetel_client_token).
        if '-web-' in (credential.username or ''):
            return False
        return True
