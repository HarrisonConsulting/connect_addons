"""Catalog boundaries do not turn private development into a purchased product."""
from contextlib import nullcontext
from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from ..models import license as license_module


@tagged('post_install', '-at_install')
class TestLicenseScope(TransactionCase):
    def setUp(self):
        super().setUp()
        self.license = self.env['oduist.license']
        self.private_name = 'connect_private_scope_test'
        license_module.PRIVATE_INTEGRATION_MODULES.add(self.private_name)
        self.addCleanup(license_module.PRIVATE_INTEGRATION_MODULES.discard, self.private_name)

    def test_private_status_avoids_token_validation_and_network(self):
        with patch.object(type(self.license), '_get_license_token', side_effect=AssertionError('token read')), \
                patch.object(license_module, 'api_call', side_effect=AssertionError('network')):
            self.assertEqual(self.license.get_license_status(self.private_name), {'status': 'private_integration'})
            self.assertEqual(self.license.get_license_status('not_a_catalog_product'), {'status': 'not_catalogued'})
            self.assertFalse(self.license.check_license(self.private_name))
            with self.assertRaises(ValidationError):
                self.license.check_license(self.private_name, silent=False)

    def test_purchase_rejects_private_unknown_and_mixed_lists_before_network(self):
        with patch.object(license_module, 'api_call', side_effect=AssertionError('network')), \
                patch.object(type(self.license), 'update_license_status', side_effect=AssertionError('refresh')):
            for names in ([self.private_name], ['not_a_catalog_product'], ['connect', self.private_name], [], 'connect'):
                with self.subTest(names=names), self.assertRaises(ValidationError):
                    self.license.buy_licenses(names)
            module = self.env['ir.module.module'].new({'name': self.private_name})
            module._compute_oduist_license_status()
            self.assertEqual(module.oduist_license_status, 'Private integration')
            self.assertFalse(module.oduist_module_purchased)
            self.assertFalse(module.oduist_module_show_price)
            with self.assertRaises(ValidationError):
                module.buy_oduist_license()

    def test_catalog_licensing_keeps_existing_entitlement_behavior(self):
        self.assertIn('connect', license_module.ODUIST_MODULES)
        for state, expected in (('production', True), ('trial_active', True), ('demo', True), ('trial_expired', False)):
            with self.subTest(state=state), patch.object(type(self.license), 'get_license_status', return_value={'status': state}):
                with self.assertLogs(license_module._logger, level='WARNING') if not expected else nullcontext():
                    self.assertEqual(self.license.check_license('connect'), expected)

    def test_check_request_contains_only_catalog_products(self):
        self.env['ir.config_parameter'].sudo().set_param('oduist_license_server', 'https://license.invalid')
        self.license.set_param('instance_uid', 'private-scope-fixture')

        class RequestObserved(Exception):
            pass

        def observe(url, payload):
            self.assertTrue(url.endswith('/license/v1/check'))
            self.assertIn('connect', payload['installed_modules'])
            self.assertNotIn(self.private_name, payload['installed_modules'])
            self.assertNotIn('connect_voicetel', payload['installed_modules'])
            self.assertNotIn('connect_textable', payload['installed_modules'])
            self.assertLessEqual(set(payload['installed_modules']), set(license_module.ODUIST_MODULES))
            raise RequestObserved()

        with patch.object(license_module, 'api_call', side_effect=observe), self.assertRaises(RequestObserved):
            self.license.update_license_status()
