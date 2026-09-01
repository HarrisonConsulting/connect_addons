# -*- coding: utf-8 -*-
"""Tests for connect.settings model."""

import os
import tempfile
from unittest.mock import patch

from odoo.tools import config
from odoo.tests import tagged
from .common import ConnectTestCase
from ..models import settings as settings_module


@tagged('post_install', '-at_install')
class TestSettings(ConnectTestCase):
    """Test connect.settings model functionality."""

    def test_settings_singleton(self):
        """Test that get_param returns consistent settings."""
        result1 = self.env['connect.settings'].get_param('debug_mode')
        result2 = self.env['connect.settings'].get_param('debug_mode')
        self.assertEqual(result1, result2)

    def test_get_param(self):
        """Test get_param method."""
        result = self.env['connect.settings'].get_param('twilio_region')
        # Should return a value or False, not raise error
        self.assertIn(type(result), [str, bool, type(None)])

    def test_set_param(self):
        """Test set_param method."""
        test_value = 'AC_test_sid_12345'
        self.env['connect.settings'].set_param('account_sid', test_value)
        result = self.env['connect.settings'].get_param('account_sid')
        self.assertEqual(result, test_value)


@tagged('post_install', '-at_install')
class TestEnvCredentialOverride(ConnectTestCase):
    """CONNECT_* environment / .env overrides for credential params."""

    def _force_runtime_mode(self):
        """Open the test-mode gate so the override path is exercised."""
        return patch.dict(config.options, {'test_enable': False})

    def test_stands_down_under_test_mode(self):
        """With test_enable on (as in this very run), no override applies."""
        with patch.dict(os.environ, {'CONNECT_ACCOUNT_SID': 'ACenv'}):
            self.assertIsNone(settings_module.get_env_credential('account_sid'))

    def test_environ_overrides_db(self):
        """A CONNECT_* process env var beats the stored settings value."""
        self.env['connect.settings'].set_param('account_sid', 'ACdb')
        with self._force_runtime_mode(), \
                patch.dict(os.environ, {'CONNECT_ACCOUNT_SID': 'ACenv'}):
            self.assertEqual(
                self.env['connect.settings'].get_param('account_sid'), 'ACenv')
        # Gate closed again: DB value visible.
        self.assertEqual(
            self.env['connect.settings'].get_param('account_sid'), 'ACdb')

    def test_non_credential_param_unaffected(self):
        """Only the allowlisted credential params consult the environment."""
        with self._force_runtime_mode(), \
                patch.dict(os.environ, {'CONNECT_DEBUG_MODE': '1'}):
            self.assertIsNone(settings_module.get_env_credential('debug_mode'))

    def test_dotenv_file_parsed(self):
        """KEY=VALUE lines (with quotes/comments) load from the .env file."""
        with tempfile.NamedTemporaryFile('w', suffix='.env', delete=False) as f:
            f.write('# comment\n'
                    'CONNECT_TWILIO_API_KEY="SKdotenv"\n'
                    'CONNECT_TWILIO_API_SECRET=plain_secret\n'
                    'NOT_A_KV_LINE\n')
            path = f.name
        try:
            with self._force_runtime_mode(), \
                    patch.object(settings_module, 'DOTENV_PATH', path), \
                    patch.dict(settings_module._dotenv_cache,
                               {'mtime': None, 'values': {}}), \
                    patch.dict(os.environ):
                os.environ.pop('CONNECT_TWILIO_API_KEY', None)
                os.environ.pop('CONNECT_TWILIO_API_SECRET', None)
                self.assertEqual(
                    settings_module.get_env_credential('twilio_api_key'),
                    'SKdotenv')
                self.assertEqual(
                    settings_module.get_env_credential('twilio_api_secret'),
                    'plain_secret')
        finally:
            os.unlink(path)

    def test_environ_beats_dotenv(self):
        """Process environment takes precedence over the .env file."""
        with tempfile.NamedTemporaryFile('w', suffix='.env', delete=False) as f:
            f.write('CONNECT_AUTH_TOKEN=from_file\n')
            path = f.name
        try:
            with self._force_runtime_mode(), \
                    patch.object(settings_module, 'DOTENV_PATH', path), \
                    patch.dict(settings_module._dotenv_cache,
                               {'mtime': None, 'values': {}}), \
                    patch.dict(os.environ,
                               {'CONNECT_AUTH_TOKEN': 'from_environ'}):
                self.assertEqual(
                    settings_module.get_env_credential('auth_token'),
                    'from_environ')
        finally:
            os.unlink(path)


@tagged('post_install', '-at_install')
class TestSecurityPreflight(ConnectTestCase):
    """Connect configuration is audited once activation intent exists."""

    def _check(self, **values):
        settings = self.env['connect.settings'].sudo().search([], limit=1)
        settings.write({
            'is_registered': False,
            'rest_provider': 'twilio',
            'twilio_verify_requests': True,
            'account_sid': False,
            'auth_token': False,
            **values,
        })
        with patch.dict(config.options, {
                'test_enable': False,
                'proxy_mode': True,
        }), patch.object(
            settings_module, 'get_env_credential', return_value=None,
        ), patch.object(
            settings_module.Settings, '_is_neutralized', return_value=False,
        ):
            return settings.check_security_preflight()

    def test_virgin_default_configuration_has_no_findings(self):
        self.assertEqual(self._check(), [])

    def test_virgin_disabled_verification_has_no_findings(self):
        self.assertEqual(self._check(twilio_verify_requests=False), [])

    def test_registered_instance_requires_credentials(self):
        with self.assertLogs(settings_module.logger, level='CRITICAL'):
            findings = self._check(is_registered=True)
        self.assertEqual(
            [(finding['code'], finding['level']) for finding in findings],
            [('twilio_credentials_missing', 'critical')],
        )

    def test_partial_credentials_activate_the_preflight(self):
        with self.assertLogs(settings_module.logger, level='CRITICAL'):
            findings = self._check(account_sid='ACpartial')
        self.assertEqual(
            [(finding['code'], finding['level']) for finding in findings],
            [('twilio_credentials_missing', 'critical')],
        )

    def test_registered_instance_requires_verification(self):
        with self.assertLogs(settings_module.logger, level='CRITICAL'):
            findings = self._check(
                is_registered=True,
                account_sid='ACconfigured',
                auth_token='configured-token',
                twilio_verify_requests=False,
            )
        self.assertEqual(
            [(finding['code'], finding['level']) for finding in findings],
            [('twilio_verify_requests_disabled', 'critical')],
        )


@tagged('post_install', '-at_install')
class TestSettingsTwilioClient(ConnectTestCase):
    """Test Twilio client creation."""

    def test_get_client_with_mock(self):
        """Test get_client with mocked Twilio."""
        with self.mockTwilioClient() as mock_client:
            client = self.env['connect.settings'].get_client()
            self.assertIsNotNone(client)
            self.assertEqual(client.region, 'us1')


@tagged('post_install', '-at_install')
class TestVoiceMLCompatSettings(ConnectTestCase):

    def test_sip_suffix_defaults_to_twilio(self):
        settings = self.env['connect.settings']
        settings.set_param('sip_domain_suffix', False)
        self.assertEqual(settings.normalized_sip_domain_suffix(), 'sip.twilio.com')

    def test_get_client_ignores_region_token_with_rest_host(self):
        settings = self.env['connect.settings']
        settings.set_param('account_sid', 'ACtest')
        settings.set_param('auth_token', 'primary_token')
        settings.set_param('region_auth_token', 'region_token')
        settings.set_param('rest_api_host', 'voiceml.example.com')
        # Must patch through the odoo.addons path: Odoo registers addon
        # modules under odoo.addons.<module>, and patching 'connect.models…'
        # imports a second copy of the module, which the framework rejects
        # with "Invalid import of connect.models.http.IrHttp".
        with patch('odoo.addons.connect.models.settings.Client') as mock_client_cls:
            settings.get_client()
            args, kwargs = mock_client_cls.call_args
            self.assertEqual(args[1], 'primary_token')
            self.assertIsNotNone(kwargs.get('http_client'))
