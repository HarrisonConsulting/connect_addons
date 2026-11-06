# -*- coding: utf-8 -*-
"""What a provider can do on a call comes from the methods it implements.

Nothing here depends on a provider being installed. Twilio's own answer is in
connect_twilio's TestCapabilitiesTwilio.
"""

from unittest.mock import patch
from uuid import uuid4

from odoo import api
from odoo.tests import TransactionCase, new_test_user, tagged

FAKE = 'capability_fake'
ACTIONS = {
    'hold', 'resume', 'attended_start', 'attended_complete', 'attended_cancel',
    'add_participant', 'forward', 'recording_state', 'recording_start',
    'recording_pause', 'recording_resume', 'recording_stop',
}


@tagged('post_install', '-at_install')
class TestSoftphoneCapabilities(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Channel = self.env['connect.channel']

    def _implement(self, *methods):
        for method in methods:
            patcher = patch.object(
                type(self.Channel), '%s_%s' % (method, FAKE), lambda *a: None, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _on(self, provider=FAKE):
        caps = self.Channel.softphone_capabilities(provider)
        self.assertEqual(set(caps), ACTIONS)
        return {action for action, able in caps.items() if able}

    def test_no_methods_no_capabilities(self):
        self.assertEqual(self._on(), set())

    def test_no_provider_no_capabilities(self):
        self.assertEqual(self._on(''), set())
        self.assertEqual(self._on(False), set())

    def test_every_action_answers_to_its_own_method(self):
        for action in ACTIONS:
            method = '_softphone_%s_%s' % (action, FAKE)
            with self.subTest(action=action), patch.object(
                    type(self.Channel), method, lambda *a: None, create=True):
                self.assertEqual(self._on(), {action})

    def test_a_recording_resume_is_not_a_call_resume(self):
        self._implement('_softphone_recording_resume')
        self.assertEqual(self._on(), {'recording_resume'})

    def test_a_call_resume_is_not_a_recording_resume(self):
        self._implement('_softphone_resume')
        self.assertEqual(self._on(), {'resume'})

    def test_the_hook_narrows(self):
        self._implement('_softphone_hold', '_softphone_recording_start')

        @api.model
        def narrow(model, provider, caps):
            return dict(caps, recording_start=False) if provider == FAKE else caps

        self.patch(type(self.Channel), '_softphone_capabilities_narrow', narrow)
        self.assertEqual(self._on(), {'hold'})

    def test_a_line_record_carries_its_providers_capabilities(self):
        self._implement('_softphone_forward')

        @api.model
        def get_client_token(model, nonce=False):
            return {'token': 'capability-token'}

        @api.model
        def _get_phone_provider(model):
            return FAKE

        ConnectUser = type(self.env['connect.user'])
        self.patch(ConnectUser, 'get_client_token', get_client_token)
        self.patch(ConnectUser, '_get_phone_provider', _get_phone_provider)
        odoo_user = new_test_user(self.env, login='capabilities-%s' % uuid4().hex)
        result = self.env['connect.user'].with_user(odoo_user).get_phone_lines('tab')
        self.assertEqual(len(result['lines']), 1)
        line = result['lines'][0]
        self.assertEqual(line['capabilities'], self.Channel.softphone_capabilities(FAKE))
        self.assertTrue(line['capabilities']['forward'])
        self.assertFalse(line['capabilities']['hold'])
        self.assertNotIn('capabilities', line['token_data'])
