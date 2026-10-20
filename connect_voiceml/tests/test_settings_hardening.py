# -*- coding: utf-8 -*-
from unittest.mock import MagicMock, patch

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


class FakeApiError(Exception):
    def __init__(self, status_code):
        super().__init__('api error %s' % status_code)
        self.status_code = status_code


@tagged('post_install', '-at_install')
class TestSettingsHardening(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Settings = self.env['connect.settings']
        self.icp = self.env['ir.config_parameter'].sudo()
        self.icp.set_param('connect.voiceml_allow_custom_host', '')

    def test_field_groups(self):
        fields_ = self.Settings._fields
        self.assertEqual(fields_['voiceml_base_url'].groups, 'base.group_system')
        self.assertEqual(fields_['voiceml_wss_url'].groups, 'base.group_system')

    def test_endpoint_constraint(self):
        check = self.Settings._check_voiceml_endpoints
        for field, value in (
                ('voiceml_base_url', 'http://voiceml.voicetel.com'),
                ('voiceml_base_url', 'https://evil.example.com'),
                ('voiceml_wss_url', 'ws://x.vml.voice.tel'),
                ('voiceml_wss_url', 'wss://x.example.com:8443')):
            fake = MagicMock()
            fake.env = self.env
            fake.sudo.return_value = [{'voiceml_base_url': '', 'voiceml_wss_url': '', field: value}]
            with self.assertRaises(ValidationError, msg=value):
                check.__func__(fake)
        ok = MagicMock()
        ok.env = self.env
        ok.sudo.return_value = [{
            'voiceml_base_url': 'https://voiceml.voicetel.com',
            'voiceml_wss_url': 'wss://d.vml.voicetel.com:8443'}]
        check.__func__(ok)
        self.icp.set_param('connect.voiceml_allow_custom_host', '1')
        custom = MagicMock()
        custom.env = self.env
        custom.sudo.return_value = [{
            'voiceml_base_url': 'https://voiceml.example.com', 'voiceml_wss_url': ''}]
        check.__func__(custom)

    def test_media_auth_requires_https(self):
        with patch.object(type(self.Settings), '_voiceml_api_host',
                          return_value='voiceml.voicetel.com'):
            self.assertIsNone(
                self.Settings._get_media_auth('http://voiceml.voicetel.com/x'))

    def test_application_unlink_only_swallows_404(self):
        app = self.env['connect.voiceml.application']
        rec = app.new({'sid': 'AP1'})
        for status in (404, 500):
            client = MagicMock()
            client.applications.delete.side_effect = FakeApiError(status)
            with patch.object(type(self.Settings), '_voiceml_client', return_value=client), \
                    patch('odoo.models.BaseModel.unlink', return_value=True):
                if status == 404:
                    self.assertTrue(rec.unlink())
                else:
                    with self.assertRaises(FakeApiError):
                        rec.unlink()

    def test_provider_log_context_names_account(self):
        self.Settings.set_param('rest_provider', 'voiceml')
        self.Settings.set_param('voiceml_account_sid', 'VMABCDEF123456')
        context = self.Settings._provider_log_context()
        self.assertIn('provider=voiceml', context)
        self.assertIn('VMABCDEF', context)
        self.assertNotIn('123456', context)
