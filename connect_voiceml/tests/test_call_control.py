# -*- coding: utf-8 -*-
from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCallControl(TransactionCase):
    """VoiceML's side of core's hold / attended transfer / forward seam."""

    def setUp(self):
        super().setUp()
        Settings = self.env['connect.settings']
        Settings.set_param('api_url', 'https://odoo.example.com/')
        Settings.set_param('call_duration_limit', '3600')
        self.env.company.country_id = self.env.ref('base.us')
        res_user = self.env['res.users'].create({
            'name': 'Agent', 'login': 'cc_agent',
            'group_ids': [(4, self.env.ref('connect.group_user').id)],
        })
        self.agent = self.env['connect.user'].create({
            'user': res_user.id, 'name': 'Agent', 'voiceml_username': 'cc1'})
        self.env['connect.voiceml.outgoing_callerid'].search([]).write(
            {'is_default': False})
        self.env['connect.voiceml.outgoing_callerid'].create({
            'number': '+15550000009', 'friendly_name': 'Main',
            'is_default': True})
        Channel = self.env['connect.channel'].sudo()
        self.call = self.env['connect.call'].sudo().create({})
        self.ours = Channel.create({
            'sid': 'CAours', 'status': 'in-progress', 'call': self.call.id,
            'technical_direction': 'inbound',
            'caller_pbx_user': self.agent.id,
            'caller_user': res_user.id})
        self.theirs = Channel.create({
            'sid': 'CAtheirs', 'status': 'in-progress', 'call': self.call.id,
            'technical_direction': 'outbound-dial'})
        self.Channel = self.env['connect.channel'].with_user(res_user)
        self.client = MagicMock()
        patcher = patch.object(
            type(self.env['connect.settings']), '_voiceml_client',
            return_value=self.client)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _payload(self, **extra):
        return dict({'provider': 'voiceml', 'channel_sid': 'CAours'}, **extra)

    def test_hold_and_resume_dispatch_to_voiceml(self):
        held = self.Channel.hold_softphone_call(self._payload())
        self.assertTrue(held['success'])
        self.assertTrue(held['is_on_hold'])
        self.assertEqual(held['channel_sid'], 'CAours')
        resumed = self.Channel.resume_softphone_call(self._payload())
        self.assertFalse(resumed['is_on_hold'])

    def test_hold_without_sid_finds_the_users_live_leg(self):
        result = self.Channel.hold_softphone_call({'provider': 'voiceml'})
        self.assertEqual(result['channel_sid'], 'CAours')

    def test_hold_refused_after_the_call_ended(self):
        self.ours.status = 'completed'
        with self.assertRaises(UserError):
            self.Channel.hold_softphone_call(self._payload())

    def test_attended_start_and_cancel_hold_and_resume(self):
        start = self.Channel.attended_transfer_softphone_call(
            self._payload(step='start'))
        self.assertTrue(start['is_on_hold'])
        cancel = self.Channel.attended_transfer_softphone_call(
            self._payload(step='cancel'))
        self.assertFalse(cancel['is_on_hold'])
        self.client.calls.update.assert_not_called()

    def test_attended_complete_moves_the_other_leg_to_the_target(self):
        result = self.Channel.attended_transfer_softphone_call(
            self._payload(step='complete', number='+15551234567'))
        self.assertTrue(result['success'])
        self.client.calls.update.assert_called_once()
        sid, body = self.client.calls.update.call_args.args
        self.assertEqual(sid, 'CAtheirs')
        twiml = body.twiml
        self.assertIn('+15551234567', twiml)
        self.assertIn('+15550000009', twiml)

    def test_blind_forward_moves_the_other_leg(self):
        self.Channel.forward_softphone_call(
            self._payload(number='+15551234567'))
        self.client.calls.update.assert_called_once()
        self.assertEqual(self.client.calls.update.call_args.args[0], 'CAtheirs')

    def test_transfer_needs_a_number_and_a_single_other_party(self):
        with self.assertRaises(UserError):
            self.Channel.forward_softphone_call(self._payload(number=' '))
        self.theirs.status = 'completed'
        with self.assertRaises(UserError):
            self.Channel.forward_softphone_call(
                self._payload(number='+15551234567'))

    def test_remote_failure_is_a_user_error(self):
        self.client.calls.update.side_effect = RuntimeError('boom')
        with self.assertRaises(UserError):
            self.Channel.forward_softphone_call(
                self._payload(number='+15551234567'))

    def test_add_participant_is_refused_in_words(self):
        self.assertFalse(
            self.Channel.softphone_call_control_supported('voiceml'))
        with self.assertRaises(UserError):
            self.Channel.add_softphone_participant(
                self._payload(number='+15551234567'))

    def _enable_web_phone(self):
        domain = self.env['connect.voiceml.domain'].with_context(
            install_mode=True).create({'domain_name': 'cc.example.com'})
        self.agent.with_context(skip_voiceml_sync=True).write({
            'voiceml_domain': domain.id, 'voiceml_client_enabled': True})

    def test_phone_config_instance_id_is_per_tab(self):
        self._enable_web_phone()
        Cfg = self.env['connect.user'].with_user(self.Channel.env.user)
        tab_a = Cfg.get_voiceml_phone_config(nonce='tab-a')
        tab_a_again = Cfg.get_voiceml_phone_config(nonce='tab-a')
        tab_b = Cfg.get_voiceml_phone_config(nonce='tab-b')
        self.assertTrue(tab_a['ok'])
        self.assertTrue(tab_a['instance_id'])
        self.assertEqual(tab_a['instance_id'], tab_a_again['instance_id'])
        self.assertNotEqual(tab_a['instance_id'], tab_b['instance_id'])

    def test_phone_config_wss_url_is_never_empty(self):
        self._enable_web_phone()
        Cfg = self.env['connect.user'].with_user(self.Channel.env.user)
        for blank in ('', '   '):
            self.env['connect.settings'].set_param('voiceml_wss_url', blank)
            self.assertEqual(
                Cfg.get_voiceml_phone_config(nonce='t')['wss_url'],
                'wss://cc.example.com:8443')
