# -*- coding: utf-8 -*-
"""A plain Twilio person calls with the real mint and originate, holding no write."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged

from .common import TwilioTestCommon


@tagged('post_install', '-at_install')
class TestToolRightsTwilio(TwilioTestCommon):

    def setUp(self):
        super().setUp()
        Settings = self.env['connect.settings'].sudo()
        Settings.set_param('account_sid', 'AC' + '0' * 32)
        Settings.set_param('twilio_api_key', 'SK' + '0' * 32)
        Settings.set_param('twilio_api_secret', 's' * 32)
        tag = uuid4().hex[:12]
        domain = self.env['connect.twilio.domain'].with_context(
            no_twilio_create=True,
        ).create({'friendly_name': 'Rights %s' % tag, 'subdomain': 'rights%s' % tag})
        self.plain = new_test_user(
            self.env, login='tool-rights-twilio-%s' % tag,
            groups='base.group_user,connect.group_user')
        self.person = self.env['connect.user'].with_context(
            no_clear_cache=True, no_twilio_create=True,
        ).create({
            'user': self.plain.id,
            'username': 'rights%s' % tag,
            'domain': domain.id,
            'client_enabled': True,
            'sip_enabled': False,
            'originate_provider': 'twilio',
        })
        self.tool = self.env['connect.tool'].create({'name': 'Twilio', 'adapter': 'twilio'})
        self.row = self.env['connect.user.tool'].create({
            'user_id': self.person.id,
            'tool_id': self.tool.id,
            'role': 'active',
            'use': 'voice',
        })

    def test_cannot_change_the_row_it_calls_on(self):
        with self.assertRaises(AccessError):
            self.row.with_user(self.plain).write({'sequence': 99})
        with self.assertRaises(AccessError):
            self.tool.with_user(self.plain).write({'name': 'Mine'})

    def test_mints_a_token(self):
        result = self.env['connect.user'].with_user(self.plain).get_phone_lines('tab-1')
        self.assertFalse(result['error'])
        self.assertEqual(len(result['lines']), 1)
        self.assertEqual(result['lines'][0]['provider'], 'twilio')
        self.assertTrue(result['lines'][0]['token_data']['token'])

    def test_originates(self):
        fake = MagicMock()
        fake.calls.create.return_value.sid = 'CAtest0000000000000000000000000002'
        settings = self.env['connect.settings'].with_user(self.plain)
        with patch.object(type(settings), 'get_client', return_value=fake):
            settings.with_context(connect_tool_id=self.tool.id).originate_call('+15550100')
        fake.calls.create.assert_called_once()
        self.assertIn('client:', fake.calls.create.call_args.kwargs['to'])
