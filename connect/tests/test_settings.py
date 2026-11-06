# -*- coding: utf-8 -*-
"""Tests for connect.settings model."""

import importlib.util
import os
from pathlib import Path
from unittest.mock import patch

from odoo.tools import config
from odoo.tests import new_test_user, tagged
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

    def test_protected_key_reaches_the_parameter_it_is_read_from(self):
        """A key entered in its display field is stored where get_param reads
        it, and the display parameter keeps only the mask."""
        key = 'sk-protected-%s' % os.urandom(4).hex()
        settings = self.env['connect.settings'].sudo().search([], limit=1)
        self.assertTrue(settings.write({'display_openai_api_key': key}))
        icp = self.env['ir.config_parameter'].sudo()
        self.assertEqual(icp.get_param('connect.openai_api_key'), key)
        self.assertEqual(icp.get_param('connect.display_openai_api_key'), '*' * len(key))
        self.assertEqual(self.env['connect.settings'].sudo().get_param('openai_api_key'), key)


@tagged('post_install', '-at_install')
class TestReadIcpUsesGetParam(ConnectTestCase):
    """A stored setting is read through ir.config_parameter.get_param, the
    method a secrets module overrides, and only after the group check."""

    def _resolving(self, value):
        """Stand in for a get_param override, leaving every other key alone."""
        IrConfig = type(self.env['ir.config_parameter'])
        original = IrConfig.get_param
        asked = []

        def get_param(model, key, default=False):
            asked.append(key)
            return value if key == 'connect.openai_api_key' else original(model, key, default)
        return patch.object(IrConfig, 'get_param', get_param), asked

    def test_an_override_of_get_param_is_honoured(self):
        icp = self.env['ir.config_parameter'].sudo()
        icp.set_param('connect.openai_api_key', 'a-stored-reference')
        override, _asked = self._resolving('the-resolved-value')
        with override:
            self.assertEqual(
                self.env['connect.settings'].sudo().get_param('openai_api_key'),
                'the-resolved-value')
        self.assertEqual(
            self.env['connect.settings'].sudo().get_param('openai_api_key'),
            'a-stored-reference')

    def test_someone_outside_the_group_never_reaches_get_param(self):
        icp = self.env['ir.config_parameter'].sudo()
        icp.set_param('connect.openai_api_key', 'a-stored-reference')
        plain = new_test_user(self.env, login='read_icp_plain', groups='base.group_user')
        field = self.env['connect.settings']._fields['openai_api_key']
        with patch.object(field, 'groups', 'base.group_system'):
            override, asked = self._resolving('the-resolved-value')
            with override:
                self.assertFalse(
                    self.env['connect.settings'].with_user(plain)._read_icp('openai_api_key'))
        self.assertNotIn('connect.openai_api_key', asked)

    def test_no_row_stays_no_answer(self):
        self.env['ir.config_parameter'].sudo().search(
            [('key', '=', 'connect.openai_api_key')]).unlink()
        self.assertIsNone(self.env['connect.settings'].sudo()._read_icp('openai_api_key'))

    def test_values_are_still_parsed_by_field_type(self):
        icp = self.env['ir.config_parameter'].sudo()
        icp.set_param('connect.debug_mode', 'True')
        self.assertIs(self.env['connect.settings'].sudo()._read_icp('debug_mode'), True)
        icp.set_param('connect.debug_mode', 'False')
        self.assertIs(self.env['connect.settings'].sudo()._read_icp('debug_mode'), False)


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
        version = '1.30.12' if phase == 'pre' else '1.30.11'
        path = Path(__file__).parents[1] / 'migrations' / version / f'{phase}-migrate.py'
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
            metadata = self.env['ir.model.data'].create({'module': module, 'name': name, **values})
        metadata.flush_recordset(['model', 'res_id'])
        self.env.registry.clear_cache()
        self.assertEqual(self.env.ref(xmlid), record)

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
