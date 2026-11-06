# -*- coding: utf-8 -*-
"""A line is a tool, projected from a person's rows and never stored.

The mint is patched, and so are the selection options, so these cases run the same
on a database with connect alone, where only sip exists. Real Twilio is in
connect_twilio's TestPhoneLinesTwilio.
"""

import logging
from uuid import uuid4

from odoo import api
from odoo.exceptions import AccessError
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
        self.assertEqual([line['mint_tool_id'] for line in lines], [first.id, second.id])

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
        self.assertEqual([line['tool_id'] for line in result['lines']], [first.id, second.id])
        self.assertTrue(result['error'])
        self.assertEqual(self.mints, [(first.id, self.NONCE), (second.id, self.NONCE)])
        self.assertNotIn('division', ''.join(logs.output))
        failed, working = result['lines']
        self.assertEqual(failed['unavailable'], result['error'])
        self.assertEqual(failed['key'], 'twilio')
        self.assertEqual(failed['provider'], 'twilio')
        self.assertEqual(failed['label'], first.name)
        self.assertEqual(failed['token_data'], {})
        self.assertEqual(failed['mint_tool_id'], first.id)
        self.assertNotIn('unavailable', working)

    def test_a_failed_mint_stays_a_line_with_its_order(self):
        """The person's choice is still there to refuse with a reason."""
        first, second = self._tool('twilio'), self._tool('other_voice')
        self._row(first, sequence=1)
        self._row(second, sequence=2)
        self.answers[second.id] = {'error': 'The plan did not answer'}
        result = self._lines()
        self.assertEqual([line['key'] for line in result['lines']], ['twilio', 'other_voice'])
        self.assertNotIn('unavailable', result['lines'][0])
        self.assertEqual(result['lines'][1]['unavailable'], 'The plan did not answer')
        self.assertEqual(result['error'], 'The plan did not answer')

    def test_every_line_failing_keeps_every_line(self):
        first, second = self._tool('twilio'), self._tool('other_voice')
        self._row(first, sequence=1)
        self._row(second, sequence=2)
        self.answers[first.id] = {'error': 'One'}
        self.answers[second.id] = {'error': 'Two'}
        result = self._lines()
        self.assertEqual([line['unavailable'] for line in result['lines']], ['One', 'Two'])

    def test_a_row_that_mints_nothing_is_not_a_failure(self):
        """No error and no credential: this provider has no browser phone to offer."""
        first, second = self._tool('twilio'), self._tool('other_voice')
        self._row(first, sequence=1)
        self._row(second, sequence=2)
        self.answers[second.id] = {'token': False}
        result = self._lines()
        self.assertEqual([line['key'] for line in result['lines']], ['twilio'])
        self.assertIs(result['error'], False)

    def test_a_credential_with_an_error_is_a_working_line(self):
        self._row(self._tool('twilio'))
        self.answers[None] = {'token': 'still-good', 'error': 'Runs out soon'}
        result = self._lines()
        self.assertNotIn('unavailable', result['lines'][0])
        self.assertEqual(result['error'], 'Runs out soon')

    def test_mint_failure_logs_no_value(self):
        def fail(model):
            raise ValueError('secret-value')

        self.answers[None] = fail
        with self.assertLogs('odoo.addons.connect.models.user', logging.WARNING) as logs:
            result = self._lines()
        self.assertEqual(len(result['lines']), 1)
        self.assertEqual(result['lines'][0]['unavailable'], result['error'])
        self.assertTrue(result['error'])
        self.assertNotIn('secret-value', result['error'])
        self.assertNotIn('secret-value', ''.join(logs.output))

    def test_refused_mint_error_is_passed_on(self):
        self.answers[None] = {'error': 'Not today'}
        result = self._lines()
        self.assertEqual(result['error'], 'Not today')
        self.assertEqual(result['lines'][0]['unavailable'], 'Not today')
        self.assertNotIn('mint_tool_id', result['lines'][0])

    def test_a_provider_can_keep_its_only_line_silent_as_it_was(self):
        """A person with one line on such a provider has no phone, as ever."""
        self._row(self._tool('twilio'))
        self.answers[None] = {'error': 'Not today'}

        @api.model
        def keeps(model, provider):
            return False

        self.patch(type(self.env['connect.user']), '_failed_mint_keeps_the_line', keeps)
        self.assertEqual(self._lines(), {'lines': [], 'error': 'Not today'})
        self._assert_one_uncontexted_mint()

    def test_such_a_provider_keeps_its_failed_line_among_several(self):
        """With two lines the person chose one, and its refusal has to say why."""
        first, second = self._tool('twilio'), self._tool('other_voice')
        self._row(first, sequence=1)
        self._row(second, sequence=2)
        self.answers[first.id] = {'error': 'Not today'}

        @api.model
        def keeps(model, provider):
            return False

        self.patch(type(self.env['connect.user']), '_failed_mint_keeps_the_line', keeps)
        lines = self._lines()['lines']
        self.assertEqual(lines[0]['unavailable'], 'Not today')
        self.assertNotIn('unavailable', lines[1])

    def test_someone_with_no_connect_user_gets_no_line_from_a_failure(self):
        """They have no phone to put a banner on, and never had one."""
        stranger = new_test_user(self.env, login='no-connect-%s' % uuid4().hex)
        self.answers[None] = {'error': 'Not today'}
        result = self.env['connect.user'].with_user(stranger).get_phone_lines(self.NONCE)
        self.assertEqual(result, {'lines': [], 'error': 'Not today'})

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
        """The first row has no credential and no bridge media, so one contexted
        line remains. Its refresh goes under the same tool, because the uncontexted
        answer is the first row's."""
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
        self.assertEqual(result['lines'][0]['mint_tool_id'], live.id)


@tagged('post_install', '-at_install')
class TestPhoneLineMintAuthority(TransactionCase):
    """A contexted mint answers only for a line the caller holds.

    The registered wrapper is what is under test, so the mint is left unpatched:
    with connect alone it answers {'token': False}, and that is enough to see it
    was reached.
    """

    def setUp(self):
        super().setUp()
        patch_line_options(self)
        self.odoo_user = new_test_user(
            self.env, login='mint-authority-%s' % uuid4().hex, groups='base.group_user,connect.group_user')
        self.connect_user = self.env['connect.user'].with_context(
            no_clear_cache=True, no_twilio_create=True,
        ).create({'user': self.odoo_user.id})
        self.tool = self.env['connect.tool'].create({'name': 'Mine', 'adapter': 'other_voice'})
        self.foreign = self.env['connect.tool'].create({'name': 'Theirs', 'adapter': 'other_voice'})

    def _hold(self, tool, **values):
        return self.env['connect.user.tool'].create({
            'user_id': self.connect_user.id, 'tool_id': tool.id,
            'role': 'active', 'use': 'voice', **values,
        })

    def _as_person(self, tool):
        return self.env['connect.user'].with_user(self.odoo_user).with_context(connect_tool_id=tool.id)

    def test_mint_on_a_tool_with_no_row_is_refused(self):
        self._hold(self.tool)
        with self.assertRaises(AccessError):
            self._as_person(self.foreign).get_client_token('nonce')

    def test_mint_on_a_held_tool_answers(self):
        self._hold(self.tool)
        self.assertIn('token', self._as_person(self.tool).get_client_token('nonce'))

    def test_mint_with_no_context_is_not_this_fence(self):
        self.assertIn(
            'token', self.env['connect.user'].with_user(self.odoo_user).get_client_token('nonce'))

    def test_mint_for_a_person_with_no_connect_user_is_refused(self):
        stranger = new_test_user(self.env, login='mint-stranger-%s' % uuid4().hex)
        with self.assertRaises(AccessError):
            self.env['connect.user'].with_user(stranger).with_context(
                connect_tool_id=self.tool.id).get_client_token('nonce')

    def test_mint_on_a_row_that_is_not_a_line_is_refused(self):
        for values in ({'role': 'backup'}, {'use': 'message'}):
            row = self._hold(self.tool, **values)
            with self.assertRaises(AccessError):
                self._as_person(self.tool).get_client_token('nonce')
            row.unlink()

    def test_mint_on_a_tool_that_does_not_exist_is_refused(self):
        self.env.cr.execute("SELECT COALESCE(MAX(id), 0) + 1000 FROM connect_tool")
        gone = self.env.cr.fetchone()[0]
        with self.assertRaises(AccessError):
            self.env['connect.user'].with_user(self.odoo_user).with_context(
                connect_tool_id=gone).get_client_token('nonce')

    def test_superuser_mint_keeps_its_own_authority(self):
        self.assertIn(
            'token',
            self.env['connect.user'].sudo().with_context(
                connect_tool_id=self.foreign.id).get_client_token('nonce'))

    def test_lines_on_a_tool_with_no_row_are_refused(self):
        self._hold(self.tool)
        with self.assertRaises(AccessError):
            self._as_person(self.foreign).get_phone_lines('nonce')

    def test_lines_on_a_held_tool_answer(self):
        self._hold(self.tool)
        self.assertEqual(self._as_person(self.tool).get_phone_lines('nonce')['error'], False)

    def test_the_wrapper_survives_as_an_rpc_method(self):
        """call_kw dispatches on _api_model, which the wrapper has to keep."""
        method = type(self.env['connect.user']).get_client_token
        self.assertTrue(getattr(method, '_api_model', False))


@tagged('post_install', '-at_install')
class TestDirectoryPresence(TransactionCase):
    """search_directory reports presence when something keeps it."""

    def setUp(self):
        super().setUp()
        self.colleague_user = new_test_user(
            self.env, login='directory-%s' % uuid4().hex, name='Directory Colleague',
            groups='base.group_user,connect.group_user')
        self.colleague = self.env['connect.user'].with_context(
            no_clear_cache=True, no_twilio_create=True,
        ).create({'user': self.colleague_user.id})

    def _entry(self):
        entries = self.env['connect.user'].with_user(self.colleague_user).search_directory(
            'Directory Colleague')
        self.assertEqual(len(entries), 1)
        return entries[0]

    def test_presence_is_there_exactly_when_the_field_is(self):
        entry = self._entry()
        if 'presence_status' in self.env['connect.user']._fields:
            self.assertEqual(entry['presence_status'], self.colleague.presence_status)
        else:
            self.assertNotIn('presence_status', entry)

    def test_nothing_else_leaves_the_model(self):
        self.assertLessEqual(
            set(self._entry()), {'id', 'name', 'user_id', 'exten_number', 'presence_status'})
