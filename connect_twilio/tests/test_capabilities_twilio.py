# -*- coding: utf-8 -*-
"""A Twilio line can do everything, except recording until the company turns it on."""

from uuid import uuid4

from odoo import api
from odoo.tests import new_test_user, tagged

from .common import TwilioTestCommon

ACTIONS = {
    'hold', 'resume', 'attended_start', 'attended_complete', 'attended_cancel',
    'add_participant', 'forward', 'recording_state', 'recording_start',
    'recording_pause', 'recording_resume', 'recording_stop',
}


@tagged('post_install', '-at_install')
class TestCapabilitiesTwilio(TwilioTestCommon):

    def setUp(self):
        super().setUp()
        self.env.company.connect_panel_recording = True

    def test_recording_is_off_until_the_company_turns_it_on(self):
        self.env.company.connect_panel_recording = False
        caps = self.env['connect.channel'].softphone_capabilities('twilio')
        self.assertEqual({action for action, on in caps.items() if not on}, {
            'recording_state', 'recording_start', 'recording_pause',
            'recording_resume', 'recording_stop'})

    def test_all_on(self):
        caps = self.env['connect.channel'].softphone_capabilities('twilio')
        self.assertEqual(caps, dict.fromkeys(ACTIONS, True))

    def test_a_twilio_line_says_so(self):
        @api.model
        def get_client_token(model, nonce=False):
            return {'token': 'capability-token'}

        @api.model
        def _get_phone_provider(model):
            return 'twilio'

        ConnectUser = type(self.env['connect.user'])
        self.patch(ConnectUser, 'get_client_token', get_client_token)
        self.patch(ConnectUser, '_get_phone_provider', _get_phone_provider)
        odoo_user = new_test_user(
            self.env, login='capabilities-%s' % uuid4().hex,
            groups='base.group_user,connect.group_user')
        lines = self.env['connect.user'].with_user(odoo_user).get_phone_lines('tab')['lines']
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]['capabilities'], dict.fromkeys(ACTIONS, True))
