# -*- coding: utf-8 -*-
"""Neutralization blanks core Connect provider secrets."""
from odoo.tests import TransactionCase, tagged
from odoo.tools import SQL
from odoo.tools.misc import file_open

KEYS = (
    'connect.auth_token', 'connect.region_auth_token',
    'connect.twilio_api_secret', 'connect.openai_api_key',
    'connect.display_auth_token', 'connect.display_region_auth_token',
    'connect.display_twilio_api_secret', 'connect.display_openai_api_key',
    'connect.voicetel_api_key', 'connect.voicetel_account_sid',
)

CORE_COLUMNS = ('openai_api_key', 'display_openai_api_key')
TWILIO_COLUMNS = (
    'auth_token', 'region_auth_token', 'twilio_api_key', 'twilio_api_secret',
    'twilio_balance', 'display_auth_token', 'display_region_auth_token',
    'display_twilio_api_secret',
)


@tagged('post_install', '-at_install')
class TestConnectNeutralize(TransactionCase):

    def test_neutralize_blanks_secrets(self):
        icp = self.env['ir.config_parameter'].sudo()
        for key in KEYS:
            icp.set_param(key, 'secret')
        self.env.flush_all()

        with file_open('connect/data/neutralize.sql') as f:
            self.env.cr.execute(f.read())

        for key in KEYS:
            self.assertFalse(icp.get_param(key), key)

    def _settings_row(self):
        settings = self.env['connect.settings'].sudo()
        return settings.search([], limit=1) or settings.create({})

    def _run_neutralize(self):
        self.env.flush_all()
        with file_open('connect/data/neutralize.sql') as f:
            self.env.cr.execute(f.read())

    def _column_values(self, columns):
        self.env.cr.execute(
            SQL('SELECT %s FROM connect_settings', SQL(', ').join(SQL.identifier(c) for c in columns)))
        return self.env.cr.fetchall()

    def test_neutralize_blanks_core_columns_with_or_without_twilio(self):
        self._settings_row()
        self.env.cr.execute(
            "UPDATE connect_settings SET openai_api_key = 'live', display_openai_api_key = 'live'")

        self._run_neutralize()

        for row in self._column_values(CORE_COLUMNS):
            self.assertEqual(row, (None, None))

    def test_neutralize_blanks_columns_left_by_a_removed_twilio(self):
        """Columns outlive an uninstalled module, and so does what is in them."""
        self._settings_row()
        for column in TWILIO_COLUMNS:
            self.env.cr.execute(SQL(
                'ALTER TABLE connect_settings ADD COLUMN IF NOT EXISTS %s varchar',
                SQL.identifier(column)))
            self.env.cr.execute(SQL(
                "UPDATE connect_settings SET %s = 'live'", SQL.identifier(column)))

        self._run_neutralize()

        for row in self._column_values(TWILIO_COLUMNS):
            self.assertEqual(row, (None,) * len(TWILIO_COLUMNS))
