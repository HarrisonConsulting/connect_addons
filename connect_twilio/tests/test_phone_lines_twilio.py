# -*- coding: utf-8 -*-
"""A one-line Twilio person's line record, from the real mint."""

from uuid import uuid4

from odoo.tests import new_test_user, tagged

from .common import TwilioTestCommon

FROZEN_KEYS = {
    'token',
    'first_callflow',
    'edge',
    'record_calls',
    'exten',
    'outgoing_callerid',
}


@tagged('post_install', '-at_install')
class TestPhoneLinesTwilio(TwilioTestCommon):

    def setUp(self):
        super().setUp()
        Settings = self.env['connect.settings'].sudo()
        Settings.set_param('account_sid', 'AC' + '0' * 32)
        Settings.set_param('twilio_api_key', 'SK' + '0' * 32)
        Settings.set_param('twilio_api_secret', 's' * 32)
        tag = uuid4().hex[:12]
        domain = self.env['connect.twilio.domain'].with_context(
            no_twilio_create=True,
        ).create({'friendly_name': 'Lines %s' % tag, 'subdomain': 'lines%s' % tag})
        self.odoo_user = new_test_user(
            self.env, login='phone-lines-%s' % tag,
            groups='base.group_user,connect.group_user')
        self.connect_user = self.env['connect.user'].with_context(
            no_clear_cache=True, no_twilio_create=True,
        ).create({
            'user': self.odoo_user.id,
            'username': 'lines%s' % tag,
            'domain': domain.id,
            'client_enabled': True,
            'sip_enabled': False,
            'originate_provider': 'twilio',
        })

    def _row(self, adapter, **values):
        tool = self.env['connect.tool'].create({'name': adapter, 'adapter': adapter})
        self.env['connect.user.tool'].create({
            'user_id': self.connect_user.id,
            'tool_id': tool.id,
            'role': 'active',
            'use': 'voice',
            **values,
        })
        return tool

    def _assert_one_twilio_line(self, tool_id):
        result = self.env['connect.user'].with_user(self.odoo_user).get_phone_lines('tab-1')
        self.assertFalse(result['error'])
        self.assertEqual(len(result['lines']), 1)
        line = result['lines'][0]
        self.assertEqual(line['provider'], 'twilio')
        self.assertNotIn('provider', line['token_data'])
        self.assertEqual(set(line['token_data']), FROZEN_KEYS)
        self.assertTrue(line['token_data']['token'])
        self.assertEqual(line['tool_id'], tool_id)
        self.assertEqual(line['media'], 'browser')
        self.assertEqual(line['key'], 'twilio')

    def test_stored_selection_and_no_row(self):
        self._assert_one_twilio_line(False)

    def test_one_row(self):
        self._assert_one_twilio_line(self._row('twilio').id)

    def test_twilio_row_plus_sip_row(self):
        tool = self._row('twilio', sequence=1)
        self._row('sip', sequence=2)
        self._assert_one_twilio_line(tool.id)

    def test_twilio_row_plus_row_on_each_other_non_voice_adapter(self):
        """Whatever else is installed: an adapter that is no phone provider, used
        for both, leaves a Twilio person on one line."""
        tool = self._row('twilio', sequence=1)
        adapters = self.env['connect.tool']._fields['adapter'].get_values(self.env)
        options = self.env['connect.user']._fields['originate_provider'].get_values(self.env)
        for sequence, adapter in enumerate(sorted(set(adapters) - set(options)), start=2):
            self._row(adapter, sequence=sequence, use='both')
        self._assert_one_twilio_line(tool.id)
