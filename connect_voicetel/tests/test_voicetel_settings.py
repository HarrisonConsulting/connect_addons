# -*- coding: utf-8 -*-
from odoo.tests import TransactionCase, tagged
from odoo.exceptions import ValidationError


@tagged('post_install', '-at_install')
class TestVoicetelSettings(TransactionCase):

    def setUp(self):
        super().setUp()
        Settings = self.env['connect.settings'].sudo()
        self.settings = Settings.search([], limit=1) or Settings.with_context(
            no_constrains=True).create({})
        self.settings.with_context(skip_protected_fields=True).write({
            'voicetel_account_sid': 'AC_TEST_ACCOUNT_SID',
            'voicetel_api_key': 'a' * 32,
            'voicetel_rest_host': 'voicetel.example.test',
        })

    def test_base_url_from_host(self):
        self.assertEqual(
            self.settings._voicetel_base_url(), 'https://voicetel.example.test')

    def test_base_url_default_when_host_cleared(self):
        self.settings.write({'voicetel_rest_host': False})
        self.assertEqual(
            self.settings._voicetel_base_url(), 'https://voiceml.voicetel.com')

    def test_base_url_passes_through_an_explicit_scheme(self):
        self.settings.write({'voicetel_rest_host': 'http://voicetel.example.test'})
        self.assertEqual(
            self.settings._voicetel_base_url(), 'http://voicetel.example.test')

    def test_voicetel_client_uses_our_own_credentials(self):
        try:
            import voiceml
        except ImportError:
            self.skipTest('voiceml package not importable in this test environment')
        client = self.settings._voicetel_client()
        self.assertIsInstance(client, voiceml.Client)
        self.assertEqual(client.base_url, 'https://voicetel.example.test')

    def test_get_media_auth_own_host(self):
        auth = self.settings.get_media_auth(
            'https://voicetel.example.test/2010-04-01/Accounts/AC../Recordings/RE123.wav')
        self.assertEqual(auth, ('AC_TEST_ACCOUNT_SID', 'a' * 32))

    def test_get_media_auth_foreign_host(self):
        auth = self.settings.get_media_auth('https://bucket.s3.amazonaws.com/rec.wav')
        self.assertIsNone(auth)

    def test_sync_requires_credentials(self):
        self.settings.with_context(skip_protected_fields=True).write({
            'voicetel_account_sid': False,
            'voicetel_api_key': False,
        })
        with self.assertRaises(ValidationError):
            self.settings.sync_voicetel()
