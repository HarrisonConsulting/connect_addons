# -*- coding: utf-8 -*-
"""What a server-originated call tells the browser, and how the browser ends it.

Nothing here needs a provider: the one hang-up method is patched in under a name
no module has.
"""

from datetime import datetime
from unittest.mock import patch
from uuid import uuid4

from odoo.exceptions import AccessError, UserError
from odoo.service.model import get_public_method
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.connect.models.settings import CALL_STATES

FAKE = 'call_state_fake'
PAYLOAD_KEYS = {'call_ref', 'line_key', 'state', 'reason_code', 'since'}


@tagged('post_install', '-at_install')
class TestCallState(TransactionCase):

    def setUp(self):
        super().setUp()
        self.odoo_user = new_test_user(
            self.env, login='call-state-%s' % uuid4().hex,
            groups='base.group_user,connect.group_user')
        self.settings = self.env['connect.settings'].with_user(self.odoo_user)
        self.channel = self.env['connect.channel'].with_user(self.odoo_user)
        self.sent = []
        patcher = patch.object(
            type(self.env['bus.bus']), '_sendone',
            lambda model, target, kind, message: self.sent.append((target, kind, message)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _publish(self, **payload):
        self.settings._notify_call_state(self.odoo_user.id, {'call_ref': 'key-1', **payload})
        return self.sent[-1]

    def test_every_state_goes_to_that_users_channel(self):
        self.assertEqual(CALL_STATES, {
            'calling', 'first_leg_answered', 'connecting', 'in_call', 'ended', 'failed', 'unknown'})
        for state in CALL_STATES:
            with self.subTest(state=state):
                target, kind, message = self._publish(state=state, line_key='line-1')
                self.assertEqual(target, 'connect_actions_%s' % self.odoo_user.id)
                self.assertEqual(kind, 'connect_call_state')
                self.assertEqual(message['state'], state)
                self.assertEqual(message['call_ref'], 'key-1')
                self.assertEqual(message['line_key'], 'line-1')

    def test_the_message_carries_only_its_five_keys(self):
        _target, _kind, message = self._publish(
            state='in_call', number='+15550199', secret='x', provider='y')
        self.assertEqual(set(message), PAYLOAD_KEYS)

    def test_an_unknown_state_is_a_bug_and_sends_nothing(self):
        with self.assertRaises(ValueError):
            self.settings._notify_call_state(self.odoo_user.id, {'call_ref': 'k', 'state': 'ringing'})
        self.assertFalse(self.sent)

    def test_a_call_ref_is_required(self):
        with self.assertRaises(KeyError):
            self.settings._notify_call_state(self.odoo_user.id, {'state': 'calling'})
        self.assertFalse(self.sent)

    def test_reason_defaults_empty_and_keeps_a_neutral_code(self):
        self.assertEqual(self._publish(state='ended')[2]['reason_code'], '')
        self.assertEqual(self._publish(state='ended', reason_code='busy')[2]['reason_code'], 'busy')

    def test_since_defaults_to_now_and_is_a_server_string(self):
        with patch('odoo.fields.Datetime.now', return_value=datetime(2026, 11, 5, 10, 0, 0)):
            self.assertEqual(self._publish(state='calling')[2]['since'], '2026-11-05 10:00:00')

    def test_since_takes_a_datetime_or_a_string(self):
        self.assertEqual(
            self._publish(state='in_call', since=datetime(2026, 1, 2, 3, 4, 5))[2]['since'],
            '2026-01-02 03:04:05')
        self.assertEqual(
            self._publish(state='in_call', since='2026-01-02 03:04:05')[2]['since'],
            '2026-01-02 03:04:05')

    def test_publishing_is_not_reachable_from_the_browser(self):
        with self.assertRaisesRegex(AccessError, 'Private methods'):
            get_public_method(self.settings, '_notify_call_state')

    def test_hangup_dispatches_to_the_provider_with_the_payload(self):
        seen = []
        payload = {'provider': FAKE, 'call_ref': 'key-1', 'line_key': 'line-1'}
        with patch.object(
                type(self.channel), '_softphone_hangup_%s' % FAKE,
                lambda model, received: seen.append(received) or 'done', create=True):
            self.assertEqual(self.channel.hangup_softphone_call(payload), 'done')
        self.assertEqual(seen, [payload])

    def test_hangup_with_no_provider_refuses_in_words(self):
        for payload in (None, {}, {'call_ref': 'key-1'}):
            with self.subTest(payload=payload), self.assertRaisesRegex(UserError, 'Missing telephony provider'):
                self.channel.hangup_softphone_call(payload)

    def test_hangup_by_a_provider_without_it_refuses_in_words(self):
        with self.assertRaisesRegex(UserError, 'not supported by this provider'):
            self.channel.hangup_softphone_call({'provider': FAKE, 'call_ref': 'key-1'})

    def test_hangup_is_not_offered_as_a_capability(self):
        with patch.object(
                type(self.channel), '_softphone_hangup_%s' % FAKE, lambda *a: None, create=True):
            self.assertNotIn('hangup', self.channel.softphone_capabilities(FAKE))
