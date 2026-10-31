"""Offline boundary checks against actual model methods; no Odoo or network needed.

The integration cases live in connect/tests/test_license_scope.py. These narrow
checks execute the same production methods with a deliberately unavailable env.
"""
import ast
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[1]


class ValidationError(Exception):
    pass


def methods(path, class_name, names, namespace):
    parsed = ast.parse(path.read_text())
    source_class = next(n for n in parsed.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    body = [n for n in source_class.body if isinstance(n, ast.FunctionDef) and n.name in names]
    for node in body:
        node.decorator_list = []
    cls = ast.ClassDef(name=class_name, bases=[], keywords=[], body=body, decorator_list=[])
    tree = ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[]))
    exec(compile(tree, str(path), 'exec'), namespace)
    return namespace[class_name]


class UnavailableEnv:
    def __getitem__(self, key):
        raise AssertionError('environment reached before boundary: ' + key)


class IncubationContract(unittest.TestCase):
    def setUp(self):
        self.namespace = {
            'ODUIST_MODULES': ['connect', 'connect_twilio'],
            'PRIVATE_INTEGRATION_MODULES': {'connect_voicetel', 'connect_textable'},
            'ValidationError': ValidationError, '_logger': logging.getLogger('scope_fixture'),
            'urljoin': urljoin, 'release': SimpleNamespace(version_info=[19]),
        }
        cls = methods(ROOT / 'connect/models/license.py', 'OduistLicense',
                      {'get_license_status', 'check_license', 'buy_licenses', 'update_license_status'}, self.namespace)
        self.license = cls()
        self.license.env = UnavailableEnv()
        self.license.sudo = lambda: self.license
        self.license._get_license_token = lambda: self.fail('token read for excluded product')

    def test_private_and_unknown_statuses_have_no_token_or_env_access(self):
        self.assertEqual(self.license.get_license_status('connect_voicetel'), {'status': 'private_integration'})
        self.assertEqual(self.license.get_license_status('connect_textable'), {'status': 'private_integration'})
        self.assertEqual(self.license.get_license_status('unknown'), {'status': 'not_catalogued'})

    def test_excluded_names_supply_no_entitlement(self):
        for name in ('connect_voicetel', 'connect_textable', 'unknown'):
            with self.subTest(name=name):
                self.assertFalse(self.license.check_license(name))
                with self.assertRaises(ValidationError):
                    self.license.check_license(name, silent=False)

    def test_catalog_token_and_trial_statuses_are_preserved(self):
        self.license._get_license_token = lambda: 'fixture'
        self.license.validate_token = lambda token: {'purchased_modules': {'connect': {'license_type': 'production', 'order_id': 'fixture-order'}}}
        self.assertEqual(self.license.get_license_status('connect'), {'status': 'production', 'order_id': 'fixture-order'})
        self.assertTrue(self.license.check_license('connect'))
        self.license._get_license_token = lambda: ''
        self.license.is_trial_valid = lambda name: (False, 0)
        with self.assertLogs(self.namespace['_logger'], level='WARNING'):
            self.assertFalse(self.license.check_license('connect'))
        with self.assertRaises(ValidationError):
            self.license.check_license('connect', silent=False)
        self.license.is_trial_valid = lambda name: (True, 9)
        self.assertEqual(self.license.get_license_status('connect_twilio'), {'status': 'trial_active', 'days_left': 9})

    def test_private_and_mixed_purchases_fail_before_env_or_network(self):
        for names in (['connect_voicetel'], ['connect_textable'], ['unknown'], ['connect', 'connect_voicetel'], [], 'connect'):
            with self.subTest(names=names), self.assertRaises(ValidationError):
                self.license.buy_licenses(names)

    def test_module_purchase_refuses_before_refresh(self):
        cls = methods(ROOT / 'connect/models/ir_module_module.py', 'IrModuleModule',
                      {'buy_oduist_license'}, self.namespace)
        module = cls()
        module.name = 'connect_voicetel'
        module.ensure_one = lambda: None
        module.env = UnavailableEnv()
        with self.assertRaises(ValidationError):
            module.buy_oduist_license()

    def test_catalog_purchase_still_submits_only_requested_catalog_product(self):
        company = SimpleNamespace(country_id=SimpleNamespace(code='US'), vat='', name='', street='',
                                  city='', state_id=None, zip='')
        self.license.get_param = lambda key, default=False: 'fixture-instance' if key == 'instance_uid' else default
        self.license.env = {
            'ir.config_parameter': SimpleNamespace(sudo=lambda: SimpleNamespace(get_param=lambda key: 'https://license.invalid')),
            'res.company': SimpleNamespace(search=lambda *a, **kw: company),
        }
        observed = []
        self.namespace['api_call'] = lambda url, payload: observed.append((url, payload)) or {'payment_link': 'https://payment.invalid'}
        result = self.license.buy_licenses(['connect'])
        self.assertEqual(result['url'], 'https://payment.invalid')
        self.assertEqual(observed[0][0], 'https://license.invalid/license/v1/buy')
        self.assertEqual(observed[0][1]['modules'], ['connect'])

    def test_check_payload_filters_private_modules_at_search_boundary(self):
        self.license.get_param = lambda key, default=False: 'fixture-instance' if key == 'instance_uid' else default
        company = SimpleNamespace(country_id=SimpleNamespace(code='US'))
        catalog = self.namespace['ODUIST_MODULES']

        def search(domain):
            accepted = next(value for field, operator, value in domain if field == 'name' and operator == 'in')
            self.assertEqual(accepted, catalog)
            installed = ['connect', 'connect_twilio', 'connect_voicetel', 'connect_textable']
            return SimpleNamespace(mapped=lambda key: [name for name in installed if name in accepted])

        modules = SimpleNamespace(search=search)
        modules.sudo = lambda: modules
        parameters = SimpleNamespace(get_param=lambda key: 'https://license.invalid')
        parameters.sudo = lambda: parameters
        self.license.env = {
            'ir.config_parameter': parameters, 'ir.module.module': modules,
            'res.company': SimpleNamespace(search=lambda *a, **kw: company),
            'oduist.license': self.license,
        }

        class Observed(Exception):
            pass

        def observe(url, payload):
            self.assertEqual(url, 'https://license.invalid/license/v1/check')
            self.assertEqual(payload['installed_modules'], ['connect', 'connect_twilio'])
            raise Observed()

        self.namespace['api_call'] = observe
        with self.assertRaises(Observed):
            self.license.update_license_status()

    def test_display_distinguishes_private_from_purchased(self):
        cls = methods(ROOT / 'connect/models/ir_module_module.py', 'IrModuleModule',
                      {'_compute_oduist_license_status'}, self.namespace)
        records = [SimpleNamespace(name=name, oduist_module_price='stale')
                   for name in ('connect_voicetel', 'unknown', 'connect', 'connect_twilio')]
        self.license.get_license_status = lambda name: {
            'connect_voicetel': {'status': 'private_integration'},
            'unknown': {'status': 'not_catalogued'},
            'connect': {'status': 'production', 'order_id': 'fixture'},
            'connect_twilio': {'status': 'trial_expired'},
        }[name]

        class Records(list):
            env = {'oduist.license': self.license}

        cls._compute_oduist_license_status(Records(records))
        self.assertEqual(records[0].oduist_license_status, 'Private integration')
        self.assertFalse(records[0].oduist_module_purchased)
        self.assertEqual(records[0].oduist_module_show_price, '')
        self.assertEqual(records[1].oduist_license_status, '')
        self.assertFalse(records[1].oduist_module_purchased)
        self.assertEqual(records[2].oduist_license_status, 'Purchase Order: fixture')
        self.assertTrue(records[2].oduist_module_purchased)
        self.assertEqual(records[3].oduist_license_status, 'Trial Expired')


if __name__ == '__main__':
    unittest.main()
