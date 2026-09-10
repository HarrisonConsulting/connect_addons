# -*- coding: utf-8 -*-
"""Tests for connect.settings model."""

import importlib.util
import os
from pathlib import Path
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

    def test_auth_token_alone_requires_account(self):
        with self.assertLogs(settings_module.logger, level='CRITICAL'):
            findings = self._check(auth_token='partial-token')
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

    def test_configured_instance_requires_verification(self):
        with self.assertLogs(settings_module.logger, level='CRITICAL'):
            findings = self._check(
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


@tagged('post_install', '-at_install')
class TestUsageRetirement(ConnectTestCase):
    """Upgrades retire the exact scheduled job without deleting other work."""

    def _migration(self, phase):
        path = Path(__file__).parents[1] / 'migrations' / '1.30.11' / f'{phase}-migrate.py'
        spec = importlib.util.spec_from_file_location(f'connect_retirement_{phase}', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.migrate

    def _alias(self, xmlid, record):
        module, name = xmlid.split('.')
        metadata = self.env['ir.model.data'].search([
            ('module', '=', module), ('name', '=', name),
        ])
        values = {'model': record._name, 'res_id': record.id}
        if metadata:
            metadata.write(values)
        else:
            self.env['ir.model.data'].create({'module': module, 'name': name, **values})
        self.env.registry.clear_cache()

    def _cron(self, name):
        return self.env['ir.cron'].create({
            'name': name, 'model_id': self.env['ir.model']._get_id('connect.settings'),
            'state': 'code', 'code': 'pass', 'active': False,
            'interval_number': 1, 'interval_type': 'days',
        })

    def test_retirement_restores_visibility_and_is_idempotent(self):
        """The legacy filter clears; unrelated cron rows survive repeated upgrades."""
        beacon = self._cron('Obsolete job')
        notification = self._cron('Module notifications')
        unrelated = self._cron('Independent work')
        self._alias('connect.update_usage', beacon)
        self._alias('mail.ir_cron_module_update_notification', notification)
        action = self.env.ref('base.ir_cron_act')
        action.domain = repr([('id', 'not in', [notification.id, beacon.id])])
        migrate = self._migration('post')
        migrate(self.env.cr, '1.30.10')
        migrate(self.env.cr, '1.30.10')
        self.assertFalse(beacon.exists())
        self.assertTrue(notification.exists())
        self.assertTrue(unrelated.exists())
        self.assertFalse(action.domain)
        self.assertFalse(self.env.ref('connect.update_usage', raise_if_not_found=False))

    def test_custom_action_domain_survives_retirement(self):
        """Independent administrator filters are not the legacy concealment."""
        beacon = self._cron('Obsolete job')
        self._alias('connect.update_usage', beacon)
        action = self.env.ref('base.ir_cron_act')
        custom_domain = "[('active', '=', True)]"
        action.domain = custom_domain
        self._migration('post')(self.env.cr, '1.30.10')
        self.assertFalse(beacon.exists())
        self.assertEqual(action.domain, custom_domain)

    def test_stored_extension_gate_is_removed_in_every_language(self):
        """Installed child views remain valid before their own module upgrades."""
        view = self.env['ir.ui.view'].create({
            'name': 'Settings extension fixture', 'model': 'connect.settings',
            'arch': '<form><sheet><group/></sheet></form>',
        })
        self._alias('connect_enterprise.connect_enterprise_settings_form', view)
        architecture = '<form><sheet invisible="is_registered == False"/></form>'
        self.env.cr.execute(
            "UPDATE ir_ui_view SET arch_db = jsonb_build_object('en_US', %s, 'fr_FR', %s) WHERE id = %s",
            [architecture, architecture, view.id],
        )
        migrate = self._migration('pre')
        migrate(self.env.cr, '1.30.10')
        migrate(self.env.cr, '1.30.10')
        self.env.cr.execute('SELECT arch_db FROM ir_ui_view WHERE id = %s', [view.id])
        translations = self.env.cr.fetchone()[0]
        self.assertEqual(set(translations), {'en_US', 'fr_FR'})
        self.assertTrue(all('is_registered' not in value for value in translations.values()))
