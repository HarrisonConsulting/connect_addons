# -*- coding: utf-8 -*-
"""A line is a tool, projected from a person's rows and never stored.

The mint is patched, and so are the selection options, so these cases run the same
on a database with connect alone, where only sip exists. Real Twilio is in
connect_twilio's TestPhoneLinesTwilio.
"""

import logging
from uuid import uuid4

from odoo import api
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger

ADAPTERS = ['sip', 'twilio', 'other_voice', 'textable']
OPTIONS = ['twilio', 'other_voice']


def patch_line_options(case):
    """Make ADAPTERS valid on a tool and OPTIONS the phone providers, for one test."""
    fields_and_keys = (
        (case.env['connect.tool']._fields['adapter'], ADAPTERS),
        (case.env['connect.user']._fields['originate_provider'], OPTIONS),
    )
    for field, keys in fields_and_keys:
        pairs = [(key, key) for key in keys]
        case.patch(field, 'selection', pairs)
        case.patch(field, '_selection', dict(pairs))


@tagged('post_install', '-at_install')
class TestPhoneLines(TransactionCase):

    NONCE = 'tab-nonce'

    def setUp(self):
        super().setUp()
        patch_line_options(self)
        login = 'phone-lines-%s' % uuid4().hex
        self.odoo_user = new_test_user(self.env, login=login)
        self.connect_user = self.env['connect.user'].with_context(
            no_clear_cache=True, no_twilio_create=True,
        ).create({'user': self.odoo_user.id})
        self.mints = []
        # What the mint answers per context tool. Anything else gets a token.
        self.answers = {}
        case = self

        @api.model
        def get_client_token(model, nonce=False):
            tool_id = model.env.context.get('connect_tool_id')
            case.mints.append((tool_id, nonce))
            answer = case.answers.get(tool_id, {'token': 'token-%s' % tool_id})
            return answer(model) if callable(answer) else answer

        self.patch(type(self.env['connect.user']), 'get_client_token', get_client_token)

    def _tool(self, adapter, **values):
        return self.env['connect.tool'].create({
            'name': 'Tool %s' % adapter, 'adapter': adapter, **values,
        })

    def _row(self, tool, **values):
        return self.env['connect.user.tool'].create({
            'user_id': self.connect_user.id,
            'tool_id': tool.id,
            'role': 'active',
            'use': 'voice',
            **values,
        })

    def _lines(self):
        return self.env['connect.user'].with_user(self.odoo_user).get_phone_lines(self.NONCE)

    def _assert_one_uncontexted_mint(self):
        self.assertEqual(self.mints, [(None, self.NONCE)])

    def test_zero_rows_no_selection(self):
        """Nothing resolves a provider, so a line that does mint says so."""
        result = self._lines()
        self._assert_one_uncontexted_mint()
        self.assertEqual(len(result['lines']), 1)
        line = result['lines'][0]
        self.assertFalse(line['provider'])
        self.assertFalse(line['tool_id'])
        self.assertEqual(line['media'], 'browser')

    def test_zero_rows_no_credential(self):
        self.answers[None] = {'token': False}
        self.assertEqual(self._lines(), {'lines': [], 'error': False})
        self._assert_one_uncontexted_mint()

    def test_zero_rows_stored_selection(self):
        self.connect_user.originate_provider = 'twilio'
        line = self._lines()['lines'][0]
        self._assert_one_uncontexted_mint()
        self.assertEqual(line['provider'], 'twilio')
        self.assertEqual(line['key'], 'twilio')
        self.assertFalse(line['tool_id'])

    def test_no_connect_user(self):
        stranger = new_test_user(self.env, login='no-connect-%s' % uuid4().hex)
        self.answers[None] = {'token': False}
        result = self.env['connect.user'].with_user(stranger).get_phone_lines(self.NONCE)
        self.assertEqual(result, {'lines': [], 'error': False})
        self._assert_one_uncontexted_mint()

    def test_one_row(self):
        tool = self._tool('twilio')
        self._row(tool)
        result = self._lines()
        self._assert_one_uncontexted_mint()
        self.assertEqual(result['error'], False)
        self.assertEqual(result['lines'], [{
            'key': 'twilio',
            'tool_id': tool.id,
            'label': tool.name,
            'provider': 'twilio',
            'media': 'browser',
            'token_data': {'token': 'token-None'},
            'capabilities': self.env['connect.channel'].softphone_capabilities('twilio'),
            'caller_ids': [],
        }])

    def test_two_rows(self):
        first, second = self._tool('twilio'), self._tool('other_voice')
        self._row(first, sequence=1)
        self._row(second, sequence=2)
        lines = self._lines()['lines']
        self.assertEqual(self.mints, [(first.id, self.NONCE), (second.id, self.NONCE)])
        self.assertEqual([line['tool_id'] for line in lines], [first.id, second.id])
        self.assertEqual([line['provider'] for line in lines], ['twilio', 'other_voice'])
        self.assertEqual([line['key'] for line in lines], ['twilio', 'other_voice'])
        self.assertEqual(
            [line['token_data']['token'] for line in lines],
            ['token-%s' % first.id, 'token-%s' % second.id])

    def test_rows_are_not_reordered_or_written(self):
        self._row(self._tool('twilio'), sequence=1)
        self._row(self._tool('other_voice'), sequence=2)
        before = self.connect_user.originate_provider
        rows = self.connect_user.tool_assignment_ids.read(['sequence', 'role', 'use'])
        self._lines()
        self.assertEqual(self.connect_user.originate_provider, before)
        self.assertEqual(self.connect_user.tool_assignment_ids.read(['sequence', 'role', 'use']), rows)

    def test_two_tools_on_one_adapter_are_one_line(self):
        first, second = self._tool('twilio'), self._tool('twilio')
        self._row(first, sequence=1)
        self._row(second, sequence=2)
        lines = self._lines()['lines']
        self._assert_one_uncontexted_mint()
        self.assertEqual([line['tool_id'] for line in lines], [first.id])

    def test_identity_can_split_one_adapter(self):
        """The hook a provider with several accounts on one adapter overrides."""
        first, second = self._tool('twilio'), self._tool('twilio')
        self._row(first, sequence=1)
        self._row(second, sequence=2)

        @api.model
        def _phone_line_identity(model, tool):
            return 'twilio:%s' % tool.id

        self.patch(type(self.env['connect.user']), '_phone_line_identity', _phone_line_identity)
        lines = self._lines()['lines']
        self.assertEqual(self.mints, [(first.id, self.NONCE), (second.id, self.NONCE)])
        self.assertEqual(
            [line['key'] for line in lines],
            ['twilio:%s' % first.id, 'twilio:%s' % second.id])

    def test_backup_row_is_not_a_line(self):
        first = self._tool('twilio')
        self._row(first)
        self._row(self._tool('other_voice'), role='backup')
        lines = self._lines()['lines']
        self._assert_one_uncontexted_mint()
        self.assertEqual([line['tool_id'] for line in lines], [first.id])

    def test_message_row_is_not_a_line(self):
        first = self._tool('twilio')
        self._row(first)
        self._row(self._tool('other_voice'), use='message')
        self._lines()
        self._assert_one_uncontexted_mint()

    def test_retired_tool_is_not_a_line(self):
        first = self._tool('twilio')
        self._row(first, sequence=2)
        self._row(self._tool('other_voice', active=False), sequence=3)
        lines = self._lines()['lines']
        self._assert_one_uncontexted_mint()
        self.assertEqual([line['provider'] for line in lines], ['twilio'])

    def test_retired_first_row_reports_the_provider_that_minted(self):
        """first_tool does not filter on active, so the uncontexted provider can
        differ from the projected tool. The record reports what the mint used."""
        self._row(self._tool('other_voice', active=False), sequence=1)
        self._row(self._tool('twilio'), sequence=2)
        lines = self._lines()['lines']
        self._assert_one_uncontexted_mint()
        self.assertEqual(lines[0]['provider'], 'other_voice')
        self.assertEqual(lines[0]['key'], 'other_voice')
        self.assertFalse(lines[0]['tool_id'])

    def test_other_company_tool_is_not_a_line(self):
        other = self.env['res.company'].create({'name': 'Other %s' % uuid4().hex})
        self._row(self._tool('twilio'), sequence=2)
        self._row(self._tool('other_voice', company_id=other.id), sequence=3)
        self._lines()
        self._assert_one_uncontexted_mint()

    def test_tool_with_no_company_is_a_line(self):
        self._row(self._tool('twilio', company_id=False), sequence=1)
        self._row(self._tool('other_voice', company_id=False), sequence=2)
        self.assertEqual(len(self._lines()['lines']), 2)

    def test_mint_failure_costs_only_its_line(self):
        first, second = self._tool('twilio'), self._tool('other_voice')
        self._row(first, sequence=1)
        self._row(second, sequence=2)

        def fail(model):
            # A real SQL failure, so an unprotected transaction would be dead.
            model.env.cr.execute('SELECT 1/0')

        self.answers[first.id] = fail
        with mute_logger('odoo.sql_db'), \
                self.assertLogs('odoo.addons.connect.models.user', logging.WARNING) as logs:
            result = self._lines()
        self.assertEqual([line['tool_id'] for line in result['lines']], [second.id])
        self.assertTrue(result['error'])
        self.assertEqual(self.mints, [(first.id, self.NONCE), (second.id, self.NONCE)])
        self.assertNotIn('division', ''.join(logs.output))

    def test_mint_failure_logs_no_value(self):
        def fail(model):
            raise ValueError('secret-value')

        self.answers[None] = fail
        with self.assertLogs('odoo.addons.connect.models.user', logging.WARNING) as logs:
            result = self._lines()
        self.assertEqual(result['lines'], [])
        self.assertTrue(result['error'])
        self.assertNotIn('secret-value', result['error'])
        self.assertNotIn('secret-value', ''.join(logs.output))

    def test_refused_mint_error_is_passed_on(self):
        self.answers[None] = {'error': 'Not today'}
        self.assertEqual(self._lines(), {'lines': [], 'error': 'Not today'})

    def test_password_is_a_credential(self):
        self._row(self._tool('twilio'))
        self.answers[None] = {'password': 'sip-pass'}
        self.assertEqual(len(self._lines()['lines']), 1)

    def test_bridge_line_needs_no_credential(self):
        tool = self._tool('other_voice')
        self._row(tool)
        answer = {'media': 'bridge', 'label': 'Sales plan', 'caller_ids': ['+15550100']}
        self.answers[None] = answer
        line = self._lines()['lines'][0]
        self._assert_one_uncontexted_mint()
        self.assertEqual(line['media'], 'bridge')
        self.assertEqual(line['label'], 'Sales plan')
        self.assertEqual(line['caller_ids'], ['+15550100'])
        self.assertEqual(line['provider'], 'other_voice')
        self.assertEqual(line['token_data'], answer)

    def test_voice_row_plus_sip_row_is_one_line(self):
        voice = self._tool('twilio')
        self._row(voice, sequence=1)
        self._row(self._tool('sip'), sequence=2)
        lines = self._lines()['lines']
        self._assert_one_uncontexted_mint()
        self.assertEqual([line['tool_id'] for line in lines], [voice.id])

    def test_voice_row_plus_textable_row_with_use_both_is_one_line(self):
        voice = self._tool('twilio')
        self._row(voice, sequence=1)
        self._row(self._tool('textable'), sequence=2, use='both')
        lines = self._lines()['lines']
        self._assert_one_uncontexted_mint()
        self.assertEqual([line['tool_id'] for line in lines], [voice.id])

    def test_a_row_that_mints_nothing_ahead_of_a_browser_row(self):
        """Pinned as it stands, not as settled: the first row has no credential and
        no bridge media, so one contexted line remains. Its later refresh carries
        no context (one line), so it resolves to the first row and gets no token.
        That needs a ruling before this shape goes through the human smoke gate."""
        dead, live = self._tool('other_voice'), self._tool('twilio')
        self._row(dead, sequence=1)
        self._row(live, sequence=2)
        self.answers[dead.id] = {'token': False}
        result = self._lines()
        self.assertEqual(self.mints, [(dead.id, self.NONCE), (live.id, self.NONCE)])
        self.assertEqual(result['error'], False)
        self.assertEqual([line['tool_id'] for line in result['lines']], [live.id])
        self.assertEqual(result['lines'][0]['provider'], 'twilio')
        self.assertEqual(result['lines'][0]['token_data'], {'token': 'token-%s' % live.id})
