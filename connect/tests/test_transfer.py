# -*- coding: utf-8 -*-
"""The phone's Transfer button asks connect.channel to forward the call.

Core owns the entry point and each provider owns moving the leg, so nothing here
needs a provider installed. Twilio's mechanics are in connect_twilio's
TestTwilioForwardCall.
"""

from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged

FAKE = 'forward_fake'
METHOD = '_softphone_forward_%s' % FAKE


@tagged('post_install', '-at_install')
class TestForwardSoftphoneCall(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Channel = self.env['connect.channel']

    def _provider_does(self, behaviour):
        patcher = patch.object(type(self.Channel), METHOD, behaviour, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_refuses_without_a_provider(self):
        for payload in (None, {}, {'provider': '', 'number': '100'}):
            with self.assertRaisesRegex(UserError, 'Missing telephony provider'):
                self.Channel.forward_softphone_call(payload)

    def test_refuses_in_words_when_the_provider_cannot_forward(self):
        with self.assertRaisesRegex(UserError, 'not supported by this provider'):
            self.Channel.forward_softphone_call({'provider': FAKE, 'number': '100'})

    def test_hands_the_whole_payload_to_the_provider_and_returns_its_answer(self):
        seen = []

        def forward(model, payload):
            seen.append(payload)
            return {'forwarded_to': payload['number'], 'channel_sid': 'CAOTHER'}

        self._provider_does(forward)
        payload = {'provider': FAKE, 'channel_sid': 'CAMINE', 'number': '100'}
        result = self.Channel.forward_softphone_call(payload)
        self.assertEqual(seen, [payload])
        self.assertEqual(result, {'forwarded_to': '100', 'channel_sid': 'CAOTHER'})

    def test_a_provider_refusal_reaches_the_caller(self):
        """The phone keeps the call up on this, so it must not be swallowed."""
        def forward(model, payload):
            raise UserError('Could not forward the call: busy')

        self._provider_does(forward)
        with self.assertRaisesRegex(UserError, 'Could not forward the call: busy'):
            self.Channel.forward_softphone_call({'provider': FAKE, 'number': '100'})

    def test_a_plain_connect_user_may_ask(self):
        self._provider_does(lambda model, payload: {'forwarded_to': payload['number']})
        user = new_test_user(
            self.env, login='forward_plain_user',
            groups='base.group_user,connect.group_user')
        result = self.Channel.with_user(user).forward_softphone_call(
            {'provider': FAKE, 'number': '100'})
        self.assertEqual(result, {'forwarded_to': '100'})
