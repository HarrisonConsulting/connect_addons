import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class ConnectSettings(models.Model):
    _inherit = 'connect.settings'

    def write(self, vals):
        res = super().write(vals)
        # Connect's protected field dance: display_auth_token is written first,
        # then a second write() with skip_protected_fields=True sets the real
        # auth_token. We must sync on the second call (when the real values land)
        # AND on direct account_sid changes.
        if self.env.context.get('skip_protected_fields'):
            if 'auth_token' in vals or 'account_sid' in vals:
                self._sync_credentials_to_companies()
        elif 'account_sid' in vals:
            # account_sid is not a protected field, sync immediately
            self._sync_credentials_to_companies()
        return res

    def _sync_credentials_to_companies(self):
        """Push Twilio credentials from connect.settings to all companies using Twilio."""
        account_sid = self.sudo().get_param('account_sid')
        auth_token = self.sudo().get_param('auth_token')
        if not account_sid or not auth_token:
            return
        companies = self.env['res.company'].sudo().search([
            ('sms_provider', '=', 'twilio'),
        ])
        if not companies:
            return
        companies.write({
            'sms_twilio_account_sid': account_sid,
            'sms_twilio_auth_token': auth_token,
        })
        _logger.info(
            'connect_sms: synced Twilio credentials to %d companies', len(companies)
        )
