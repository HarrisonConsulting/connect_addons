# -*- coding: utf-8 -*-

import os
import shutil
import subprocess

from odoo import api, fields, models
from odoo.exceptions import UserError

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
    voicetel_migration_log = fields.Text(
        readonly=True,
        help="Output of the last twilio-migration dry run.")

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

    def action_voicetel_migration_dry_run(self):
        self.ensure_one()
        binary = shutil.which('twilio-migration')
        if not binary:
            raise UserError(
                "The 'twilio-migration' binary was not found. Install it "
                "from https://github.com/voicetel/twilio-migration and place "
                "'twilio-migration' on the Odoo server's PATH.")
        voicetel_account_sid = self.sudo().get_param('voicetel_account_sid')
        voicetel_api_key = self.sudo().get_param('voicetel_api_key')
        if not voicetel_account_sid or not voicetel_api_key:
            raise UserError(
                "Set the VoiceTel Account SID and API Key before running "
                "the migration.")
        # Not _get_rest_api_host(): the dry run must target VoiceML even
        # while rest_provider is still 'twilio' during evaluation.
        host = (self.sudo().get_param('voicetel_rest_host') or '').strip() \
            or VOICETEL_DEFAULT_HOST
        result = subprocess.run(
            [binary, '--dry-run', '--yes'],
            env=dict(
                os.environ,
                TWILIO_ACCOUNT_SID=self.sudo().get_param('account_sid') or '',
                TWILIO_AUTH_TOKEN=self.sudo().get_param('auth_token') or '',
                VOICEML_ACCOUNT_SID=voicetel_account_sid,
                VOICEML_AUTH_TOKEN=voicetel_api_key,
                VOICEML_BASE_URL='https://' + host,
            ),
            capture_output=True,
            text=True,
            timeout=600,
        )
        log = result.stdout
        if result.stderr:
            log = '{}\n{}'.format(log, result.stderr) if log else result.stderr
        self.sudo().set_param('voicetel_migration_log', log)
