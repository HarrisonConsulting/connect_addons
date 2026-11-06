# -*- coding: utf-8 -*-
"""What a Twilio mint that raises costs, with the real mint and AccessToken raising."""

from unittest.mock import patch
from uuid import uuid4

from odoo import api
from odoo.tests import new_test_user, tagged

from .common import TwilioTestCommon

INTERNAL = 'connection to internal-host:5432 refused for key SK123'


@tagged('post_install', '-at_install')
class TestFailedMintTwilio(TwilioTestCommon):

    def setUp(self):
        super().setUp()
        Settings = self.env['connect.settings'].sudo()
        Settings.set_param('account_sid', 'AC' + '0' * 32)
        Settings.set_param('twilio_api_key', 'SK' + '0' * 32)
        Settings.set_param('twilio_api_secret', 's' * 32)
        tag = uuid4().hex[:12]
        domain = self.env['connect.twilio.domain'].with_context(
            no_twilio_create=True,
        ).create({'friendly_name': 'Failed %s' % tag, 'subdomain': 'failed%s' % tag})
        self.plain = new_test_user(
            self.env, login='failed-mint-twilio-%s' % tag,
            groups='base.group_user,connect.group_user')
        self.person = self.env['connect.user'].with_context(
            no_clear_cache=True, no_twilio_create=True,
        ).create({
            'user': self.plain.id,
            'username': 'failed%s' % tag,
            'domain': domain.id,
            'client_enabled': True,
            'sip_enabled': False,
            'originate_provider': 'twilio',
        })
        self.tool = self._tool()
        self._row(self.tool, 1)
        raising = patch(
            'odoo.addons.connect_twilio.models.user.AccessToken',
            side_effect=RuntimeError(INTERNAL))
        raising.start()
        self.addCleanup(raising.stop)

    def _tool(self):
        return self.env['connect.tool'].create({
            'name': 'Failed %s' % uuid4().hex[:8], 'adapter': 'twilio'})

    def _row(self, tool, sequence):
        return self.env['connect.user.tool'].create({
            'user_id': self.person.id, 'tool_id': tool.id,
            'role': 'active', 'use': 'voice', 'sequence': sequence,
        })

    def test_one_line_gets_no_phone_and_the_error_for_the_console(self):
        """As it has always been: nothing mounts and nothing reaches the person."""
        with self.assertLogs('odoo.addons.connect_twilio.models.user', 'ERROR'):
            result = self.env['connect.user'].with_user(self.plain).get_phone_lines('tab-1')
        self.assertEqual(result, {'lines': [], 'error': INTERNAL})

    def test_one_line_mint_answers_as_it_always_has(self):
        with self.assertLogs('odoo.addons.connect_twilio.models.user', 'ERROR'):
            answer = self.env['connect.user'].with_user(self.plain).get_client_token('tab-1')
        self.assertEqual(answer, {'error': INTERNAL})

    def test_a_line_among_several_says_it_without_the_exception(self):
        second = self._tool()
        self._row(second, 2)

        @api.model
        def _phone_line_identity(model, tool):
            return 'twilio:%s' % tool.id

        self.patch(type(self.env['connect.user']), '_phone_line_identity', _phone_line_identity)
        with self.assertLogs('odoo.addons.connect_twilio.models.user', 'ERROR'):
            result = self.env['connect.user'].with_user(self.plain).get_phone_lines('tab-1')
        self.assertEqual(len(result['lines']), 2)
        for line in result['lines']:
            self.assertEqual(line['unavailable'], 'Could not get a credential for a phone line.')
        self.assertNotIn('SK123', str(result))
