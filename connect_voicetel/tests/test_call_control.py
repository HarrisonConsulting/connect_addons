# -*- coding: utf-8 -*-
"""Call control, recording policy and dialling identity through core seams."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


class _Common(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Settings = self.env['connect.settings']
        self.Settings.set_param('voicetel_auto_sync', False)
        self.env['ir.config_parameter'].sudo().set_param(
            'connect.api_url', 'https://odoo.example.com/')
        self.Settings.set_param('call_duration_limit', 3600)
        self.Settings.set_param('record_all_calls', False)
        self.env.company.country_id = self.env.ref('base.us')
        self.domain = self.env['connect.voicetel.domain'].with_context(
            no_voicetel_create=True).create({
                'friendly_name': 'CC', 'subdomain': 'cc', 'sid': 'SDcc',
                'cred_list_sid': 'CLcc'})
        self.res_user = new_test_user(
            self.env, login='cc_agent', groups='base.group_user,connect.group_user')
        self.agent = self.env['connect.user'].with_context(
            no_voicetel_create=True).create({
                'user': self.res_user.id, 'voicetel_username': 'cc1',
                'voicetel_domain': self.domain.id})
        self.agent.with_context(skip_voicetel_sync=True).voicetel_sip_enabled = True
        self.OCID = self.env['connect.voicetel.outgoing_callerid'].with_context(
            skip_validation=True)
        self.OCID.search([]).write({'is_default': False})
        self.client = MagicMock()
        patcher = patch.object(
            type(self.env['connect.settings']), 'get_voicetel_client',
            return_value=self.client)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _ocid(self, number, **vals):
        return self.OCID.create(dict({
            'number': number, 'friendly_name': number, 'callerid_type': 'number',
            'sid': 'PN' + number[1:]}, **vals))


@tagged('post_install', '-at_install')
class TestCallControl(_Common):

    def setUp(self):
        super().setUp()
        self._ocid('+15550000009', is_default=True)
        Channel = self.env['connect.channel'].sudo()
        self.call = self.env['connect.call'].sudo().create({})
        self.ours = Channel.create({
            'sid': 'CAours', 'status': 'in-progress', 'call': self.call.id,
            'technical_direction': 'inbound',
            'caller_pbx_user': self.agent.id, 'caller_user': self.res_user.id})
        self.theirs = Channel.create({
            'sid': 'CAtheirs', 'status': 'in-progress', 'call': self.call.id,
            'technical_direction': 'outbound-dial'})
        self.Channel = self.env['connect.channel'].with_user(self.res_user)

    def _payload(self, **extra):
        return dict({'provider': 'voicetel', 'channel_sid': 'CAours'}, **extra)

    def test_hold_and_resume_dispatch_to_voicetel(self):
        held = self.Channel.hold_softphone_call(self._payload())
        self.assertTrue(held['is_on_hold'])
        self.assertEqual(held['channel_sid'], 'CAours')
        self.assertFalse(self.Channel.resume_softphone_call(self._payload())['is_on_hold'])

    def test_hold_without_sid_finds_the_users_live_leg(self):
        result = self.Channel.hold_softphone_call({'provider': 'voicetel'})
        self.assertEqual(result['channel_sid'], 'CAours')

    def test_hold_refused_after_the_call_ended(self):
        self.ours.status = 'completed'
        with self.assertRaises(UserError):
            self.Channel.hold_softphone_call(self._payload())

    def test_someone_elses_call_is_refused(self):
        other = new_test_user(self.env, login='cc_other',
                              groups='base.group_user,connect.group_user')
        Channel = self.env['connect.channel'].with_user(other)
        with self.assertRaises(AccessError):
            Channel.hold_softphone_call(self._payload())
        with self.assertRaises(AccessError):
            Channel.forward_softphone_call(self._payload(number='+15551234567'))
        self.client.calls.update.assert_not_called()

    def test_attended_complete_moves_the_other_leg(self):
        app = self.env.ref('connect_voicetel.domain_route_call')
        self.domain.application = app
        result = self.Channel.attended_transfer_softphone_call(
            self._payload(step='complete', number='+15551234567'))
        self.assertTrue(result['success'])
        sid, body = self.client.calls.update.call_args[0]
        self.assertEqual(sid, 'CAtheirs')
        self.assertIn('forward_to=%2B15551234567', body.twiml)

    def test_recording_start_and_stop_write_marks(self):
        self.client.calls.start_recording.return_value = SimpleNamespace(sid='REx')
        self.client.calls.list_recordings.return_value = SimpleNamespace(recordings=[])
        started = self.Channel.start_softphone_recording(self._payload())
        self.assertEqual(started['state'], 'on')
        sid, body = self.client.calls.start_recording.call_args[0]
        self.assertEqual(sid, 'CAours')
        self.assertEqual(body.recording_channels, 'dual')
        stopped = self.Channel.stop_softphone_recording(self._payload())
        self.assertEqual(stopped['state'], 'stopped')
        kinds = self.env['connect.recording.mark'].sudo().search(
            [('channel', '=', self.ours.id)]).mapped('kind')
        self.assertEqual(sorted(kinds), ['start', 'stop'])
        # stopped means stopped for the rest of the call
        with self.assertRaises(UserError):
            self.Channel.start_softphone_recording(self._payload())


@tagged('post_install', '-at_install')
class TestDiallingIdentity(_Common):

    def test_pstn_caller_id_prefers_user_then_default_never_extension(self):
        self.assertEqual(self.agent.voicetel_pstn_caller_id(), '')
        self._ocid('+15550000001', is_default=True)
        self.assertEqual(self.agent.voicetel_pstn_caller_id(), '+15550000001')
        own = self._ocid('+15550000002')
        self.agent.voicetel_outgoing_callerid = own
        self.assertEqual(self.agent.voicetel_pstn_caller_id(), '+15550000002')

    def test_ten_digit_number_gets_the_country_prefix(self):
        dial = self.Settings._voicetel_dial_number
        self.assertEqual(dial('4845551234'), '+14845551234')
        self.assertEqual(dial('(484) 555-1234'), '+14845551234')
        self.assertEqual(dial('+14845551234'), '+14845551234')
        self.assertEqual(dial('14845551234'), '+14845551234')
        self.assertEqual(dial('101'), '101')

    def test_external_route_is_built_not_formatted(self):
        xml = self.Settings.voicetel_external_call_route(
            '+14845551234', '+15550000001" /><Play>x</Play><Dial callerId="',
            'https://x/status')
        self.assertNotIn('<Play>', xml)
        with self.assertRaises(ValidationError):
            self.Settings.voicetel_external_call_route(
                '+1484</Number><Number>+1900', '+15550000001', 'https://x/status')

    def test_originate_dials_e164_with_the_callerid_and_records_by_policy(self):
        self._ocid('+15550000001', is_default=True)
        self.client.calls.create.return_value.sid = 'CAx'
        self.Settings.set_param('record_all_calls', True)
        with patch.object(type(self.Settings), '_get_originate_provider',
                          return_value='voicetel'):
            self.Settings.with_user(self.res_user).originate_call('4845551234')
        request = self.client.calls.create.call_args[0][0]
        self.assertEqual(request.from_, '+15550000001')
        self.assertIn('+14845551234', request.twiml)
        self.assertTrue(request.record)

    def test_domain_refuses_a_foreign_sip_domain(self):
        result = self.env['connect.voicetel.domain'].route_call({
            'CallSid': 'CAforeign', 'To': 'sip:+14845551234@evil.vml.voice.tel',
            'Caller': 'sip:cc1@cc.vml.voice.tel'})
        self.assertIn('<Reject', str(result))

    def test_domain_refuses_pstn_from_an_unknown_caller(self):
        self._ocid('+15550000001', is_default=True)
        with patch.object(type(self.env['connect.call']), 'on_voicetel_call_status',
                          return_value=False):
            result = self.env['connect.voicetel.domain'].route_call({
                'CallSid': 'CAunknown', 'To': 'sip:4845551234@cc.vml.voice.tel',
                'Caller': 'sip:stranger@cc.vml.voice.tel'})
        self.assertIn('<Reject', str(result))

    def test_domain_dials_pstn_for_a_user_with_e164(self):
        self._ocid('+15550000001', is_default=True)
        with patch.object(type(self.env['connect.call']), 'on_voicetel_call_status',
                          return_value=False):
            result = str(self.env['connect.voicetel.domain'].route_call({
                'CallSid': 'CAuser', 'To': 'sip:4845551234@cc.vml.voice.tel',
                'Caller': 'sip:cc1@cc.vml.voice.tel'}))
        self.assertIn('+14845551234', result)
        self.assertIn('callerId="+15550000001"', result)
