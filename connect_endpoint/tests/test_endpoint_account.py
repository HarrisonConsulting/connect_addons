# -*- coding: utf-8 -*-

from unittest.mock import patch

from psycopg2 import IntegrityError

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged('post_install', '-at_install')
class TestEndpointAccount(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tool = cls._make_tool()

    @classmethod
    def _make_tool(cls):
        tool_model = cls.env['connect.tool']
        vals = {
            'adapter': 'sip',
            'sip_server': 'sip.lab.example',
        }
        name = tool_model._fields.get('name')
        if name and not name.compute:
            vals['name'] = 'Lab system'
        return tool_model.create(vals)

    def _account(self, endpoint, slot, **extra):
        vals = {
            'endpoint_id': endpoint.id,
            'slot': slot,
            'tool_id': self.tool.id,
        }
        vals.update(extra)
        return self.env['connect.endpoint.account'].create(vals)

    def test_accounts_register_to_the_system(self):
        endpoint = self.env['connect.endpoint'].create({'name': 'Front desk'})
        self.assertEqual(endpoint.account_ceiling(), 0)
        self.assertEqual(endpoint.line_ceiling(), 0)
        first = self._account(endpoint, 1, name='Front', sip_user='1001', transport='udp')
        second = self._account(endpoint, 2)
        self.assertFalse(second.user_id)
        self.assertEqual(first.sip_server, self.tool.sip_server)
        self.assertEqual(first.registration, 'unregistered')
        first.mark_registered()
        self.assertEqual(first.registration, 'registered')
        first.mark_unregistered()
        self.assertEqual(first.registration, 'unregistered')
        first.mark_failed()
        self.assertEqual(first.registration, 'failed')
        line = self.env['connect.endpoint.line'].create({
            'endpoint_id': endpoint.id,
            'account_id': first.id,
        })
        self.assertEqual(line.account_id, first)
        self.assertEqual(len(endpoint.account_ids), 2)

    def test_duplicate_slot_raises(self):
        endpoint = self.env['connect.endpoint'].create({'name': 'Front desk'})
        self._account(endpoint, 1)
        # SQL unique aborts the statement. The HTTP layer turns that into
        # ValidationError; tests see the database error inside a savepoint.
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'), self.cr.savepoint():
            self._account(endpoint, 1)

    def test_line_key_rejects_account_from_another_phone(self):
        left = self.env['connect.endpoint'].create({'name': 'Left'})
        right = self.env['connect.endpoint'].create({'name': 'Right'})
        account = self._account(left, 1)
        with self.assertRaises(ValidationError):
            self.env['connect.endpoint.line'].create({
                'endpoint_id': right.id,
                'account_id': account.id,
            })

    def test_positive_account_ceiling_rejects_another_account(self):
        endpoint = self.env['connect.endpoint'].create({'name': 'Capped'})
        self._account(endpoint, 1)
        with patch.object(type(endpoint), 'account_ceiling', lambda self: 1):
            with self.assertRaises(ValidationError):
                self._account(endpoint, 2)

    def test_positive_line_ceiling_rejects_another_key(self):
        endpoint = self.env['connect.endpoint'].create({'name': 'Capped lines'})
        account = self._account(endpoint, 1)
        self.env['connect.endpoint.line'].create({
            'endpoint_id': endpoint.id,
            'account_id': account.id,
        })
        with patch.object(type(endpoint), 'line_ceiling', lambda self: 1):
            with self.assertRaises(ValidationError):
                self.env['connect.endpoint.line'].create({
                    'endpoint_id': endpoint.id,
                    'account_id': account.id,
                })
