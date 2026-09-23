# -*- coding: utf-8 -*-

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestVoicetelAccountSettings(TransactionCase):

    def setUp(self):
        super().setUp()
        self.icp = self.env['ir.config_parameter'].sudo()
        Settings = self.env['connect.settings'].sudo()
        settings = Settings.search([], limit=1)
        if not settings:
            settings = Settings.with_context(no_constrains=True).create({})
        settings.set_param('rest_provider', 'voicetel')
        self.settings = settings

    def _clear_column(self, name):
        self.settings.with_context(skip_icp_sync=True).write({name: False})
        self.icp.search([('key', '=', 'connect.%s' % name)]).unlink()

    def test_credentials_prefer_parameters_when_columns_are_empty(self):
        self.icp.set_param('connect_voicetel.account_sid', 'ACparameter')
        self.icp.set_param('connect_voicetel.api_key', 'key-parameter')
        self._clear_column('voicetel_account_sid')
        self._clear_column('voicetel_api_key')
        self.assertEqual(
            self.settings._get_client_credentials(),
            ('ACparameter', 'key-parameter'),
        )

    def test_credentials_fall_back_to_columns(self):
        for key in ('connect_voicetel.account_sid', 'connect_voicetel.api_key'):
            self.icp.search([('key', '=', key)]).unlink()
        self._clear_column('voicetel_account_sid')
        self._clear_column('voicetel_api_key')
        self.settings.with_context(skip_icp_sync=True).write({
            'voicetel_account_sid': 'ACcolumn',
            'voicetel_api_key': 'key-column',
        })
        self.assertEqual(
            self.settings._get_client_credentials(),
            ('ACcolumn', 'key-column'),
        )

    def test_rest_host_falls_back_to_column_then_default(self):
        self.icp.search([('key', '=', 'connect_voicetel.rest_host')]).unlink()
        self._clear_column('voicetel_rest_host')
        self.settings.with_context(skip_icp_sync=True).write({
            'voicetel_rest_host': 'voice.example.com',
        })
        self.assertEqual(self.settings._get_rest_api_host(), 'voice.example.com')
        self._clear_column('voicetel_rest_host')
        self.assertEqual(
            self.settings._get_rest_api_host(), 'voiceml.voicetel.com')
