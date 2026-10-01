# -*- coding: utf-8 -*-

import odoo.addons.connect_endpoint as connect_endpoint
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestGrandstreamCeiling(TransactionCase):

    def test_grandstream_ceiling_and_registration(self):
        self.assertTrue(connect_endpoint)
        tool = self.env['connect.tool'].create({
            'name': 'Lab registrar',
            'adapter': 'sip',
            'sip_server': 'pbx.example.test',
        })
        endpoint = self.env['connect.endpoint'].create({
            'name': 'GRP2670',
            'vendor': 'grandstream',
        })
        self.assertEqual(endpoint.account_ceiling(), 16)
        self.assertEqual(endpoint.line_ceiling(), 12)

        inverse = endpoint._fields['account_ids'].inverse_name
        account = self.env['connect.endpoint.account'].create({
            inverse: endpoint.id,
            'tool_id': tool.id,
            'slot': 1,
            'sip_user': '1001',
            'transport': 'udp',
        })
        values = endpoint.provisioning_values(account)
        self.assertEqual(values['sip_server'], 'pbx.example.test')
        self.assertEqual(values['sip_user'], '1001')
        self.assertEqual(values['transport'], 'udp')
        self.assertEqual(values['slot'], 1)
        self.assertTrue(values['account_active'])
        self.assertNotIn('password', values)

        endpoint.apply_registration_event(account, 'register')
        self.assertEqual(account.registration, 'registered')
        endpoint.apply_registration_event(account, 'register-failed')
        self.assertEqual(account.registration, 'failed')

        plain = self.env['connect.endpoint'].create({
            'name': 'Plain desk',
            'vendor': False,
        })
        self.assertEqual(plain.account_ceiling(), 0)
        self.assertEqual(plain.line_ceiling(), 0)
