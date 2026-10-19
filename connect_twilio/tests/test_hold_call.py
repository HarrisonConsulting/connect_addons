# -*- coding: utf-8 -*-
from unittest.mock import MagicMock, patch

from twilio.base.exceptions import TwilioRestException

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged, new_test_user


@tagged('at_install', '-post_install')
class TestTwilioHoldCall(TransactionCase):
    """Hold / attended transfer on a `<Dial>` bridge, child leg first.

    Redirecting either leg of a bare `<Dial>` tears the other one down, so a
    promotion may only move the child; the parent follows through its
    `<Dial action>`, which has to find a committed `conference_pending`
    marker naming the room.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner_user = new_test_user(
            cls.env, login='tw_hold_owner',
            groups='base.group_user,connect.group_user')
        cls.other_user = new_test_user(
            cls.env, login='tw_hold_other',
            groups='base.group_user,connect.group_user')
        cls.Settings = cls.env['connect.settings']
        cls.Settings.set_param('api_url', 'https://odoo.example.com/')
        cls.Settings.set_param('twilio_edge', 'ashburn')
        cls.application = cls.env['connect.twilio.twiml'].with_context(
            install_mode=True).create({
                'name': 'Hold App', 'sid': 'AP' + '8' * 32,
                'code_type': 'twiml', 'twiml': '<Response/>',
            })
        cls.domain = cls.env['connect.twilio.domain'].with_context(
            no_twilio_create=True).create({
                'sid': 'SD' + '8' * 32, 'subdomain': 'hold',
                'domain_name': 'hold.sip.twilio.com', 'friendly_name': 'Hold',
                'application': cls.application.id,
            })
        cls.connect_user = cls.env['connect.user'].with_context(
            no_clear_cache=True, no_twilio_create=True).create({
                'user': cls.owner_user.id,
                'username': 'holdowner',
                'domain': cls.domain.id,
                'sip_enabled': False,
                'client_enabled': False,
            })
        cls.Call = cls.env['connect.call']

    # -- fixtures -------------------------------------------------------

    def _channel(self, call, sid, direction, **vals):
        vals.update({
            'sid': sid, 'call': call.id, 'status': 'in-progress',
            'technical_direction': direction,
        })
        return self.env['connect.channel'].with_context(
            tracking_disable=True).create(vals)

    def _outbound_call(self):
        """We dialled out: our leg is the parent, the callee the child."""
        call = self.Call.with_context(tracking_disable=True).create({
            'caller': '101', 'called': '+15552222222',
            'status': 'in-progress', 'direction': 'outgoing',
            'caller_user': self.owner_user.id,
        })
        self._channel(call, 'CAMINE', 'inbound',
                      caller_pbx_user=self.connect_user.id)
        self._channel(call, 'CAOTHER', 'outbound-dial')
        return call

    def _inbound_call(self):
        """The customer dialled in: they are the parent, we are the child."""
        call = self.Call.with_context(tracking_disable=True).create({
            'caller': '+15553333333', 'called': '101',
            'status': 'in-progress', 'direction': 'incoming',
            'answered_user': self.owner_user.id,
        })
        self._channel(call, 'CACUST', 'inbound')
        self._channel(call, 'CAMINE', 'outbound-dial',
                      called_pbx_user=self.connect_user.id)
        return call

    def _mock_client(self, log, redirect_error=None):
        """Twilio stand-in that records every mutating call, in order."""
        class _Calls:
            def __init__(self, sid):
                self.sid = sid

            def update(self, **kwargs):
                if redirect_error is not None:
                    raise redirect_error
                log.append(('redirect', self.sid, kwargs))
                return MagicMock()

            def fetch(self):
                leg = MagicMock()
                leg.parent_call_sid = None
                return leg

        def participant(call_sid):
            p = MagicMock()
            p.update.side_effect = (
                lambda **kw: log.append(('participant', call_sid, kw)))
            return p

        room = MagicMock()
        room.sid = 'CFROOM'
        conference = MagicMock()
        conference.participants.side_effect = participant
        client = MagicMock()
        client.calls.side_effect = _Calls
        client.calls.list.return_value = []
        client.conferences.return_value = conference
        client.conferences.list.return_value = [room]
        return client

    def _run(self, method, log, user=None, client=None, sid='CAMINE', **extra):
        """Call a connect.channel RPC with Twilio and the commit faked.

        The marker commit is replaced by a write in the test transaction
        that is logged in the same list as the Twilio calls, so order is
        observable.
        """
        client = client or self._mock_client(log)

        def commit(call, vals):
            log.append(('commit', call.id, dict(vals)))
            call.sudo().write(vals)

        payload = dict({'provider': 'twilio', 'channel_sid': sid}, **extra)
        with patch.object(type(self.Settings), 'get_client', return_value=client), \
                patch.object(type(self.Call), '_conference_state_commit', commit), \
                patch('odoo.addons.connect_twilio.models.channel.time.sleep'):
            return getattr(self.env['connect.channel'].with_user(
                user or self.owner_user), method)(payload)

    @staticmethod
    def _redirects(log):
        return [entry for entry in log if entry[0] == 'redirect']

    # -- tests ----------------------------------------------------------

    def test_outbound_hold_moves_only_the_callee(self):
        """Our leg runs the <Dial>; moving it would hang the customer up."""
        self._outbound_call()
        log = []
        self._run('hold_softphone_call', log)

        redirects = self._redirects(log)
        self.assertEqual([r[1] for r in redirects], ['CAOTHER'])
        twiml = redirects[0][2]['twiml']
        self.assertIn('<Conference', twiml)
        self.assertIn('endConferenceOnExit="false"', twiml,
                      'the customer leaving must not end the room')

    def test_inbound_hold_moves_only_the_agent(self):
        """The customer runs the <Dial> here, so our own leg is the child."""
        self._inbound_call()
        log = []
        self._run('hold_softphone_call', log)

        redirects = self._redirects(log)
        self.assertEqual([r[1] for r in redirects], ['CAMINE'])
        self.assertIn('endConferenceOnExit="true"', redirects[0][2]['twiml'])
        held = [e for e in log if e[0] == 'participant']
        self.assertEqual(held[0][1], 'CACUST', 'the customer is the one held')

    def test_marker_is_committed_before_the_redirect(self):
        """The parent's action webhook runs in another transaction."""
        call = self._outbound_call()
        log = []
        self._run('hold_softphone_call', log)

        kinds = [e[0] for e in log]
        first_commit = kinds.index('commit')
        self.assertLess(first_commit, kinds.index('redirect'))
        vals = log[first_commit][2]
        self.assertTrue(vals['conference_pending'])
        self.assertTrue(vals['conference_name'].startswith(
            'callcontrol-{}-'.format(call.id)))

    def test_action_handlers_rejoin_the_parent_once(self):
        """connect.call, connect.user and the callflow all honour the marker."""
        call = self._outbound_call()
        request = {
            'CallSid': 'CAMINE', 'CallStatus': 'in-progress',
            'DialCallStatus': 'completed', 'DialCallSid': 'CAOTHER',
        }
        handlers = {
            'connect.call': lambda: self.Call.on_call_action(dict(request)),
            'connect.user': lambda: self.env['connect.user'].on_call_action(
                self.connect_user.id, dict(request)),
            'connect.twilio.callflow': lambda: self.env[
                'connect.twilio.callflow'].on_call_action(0, dict(request)),
        }
        for name, handler in handlers.items():
            with self.subTest(handler=name):
                call.write({'conference_name': 'callcontrol-room',
                            'conference_pending': True})
                twiml = str(handler())
                self.assertIn('<Conference', twiml)
                self.assertIn('callcontrol-room', twiml)
                self.assertIn('endConferenceOnExit="true"', twiml,
                              'the parent here is the agent')
                self.assertFalse(call.conference_pending)

                # Without the marker: unchanged behaviour.
                twiml = str(handler())
                self.assertNotIn('<Conference', twiml)
                self.assertIn('<Hangup', twiml)

    def test_originate_dial_carries_an_action(self):
        """No action means Twilio ends the agent's leg when the child moves."""
        self.env['connect.twilio.outgoing_callerid'].create({
            'friendly_name': 'Default', 'number': '+15550007777',
            'callerid_type': 'number', 'is_default': True,
        })
        self.env['connect.whatsapp_sender'].sudo().create({
            'number': '+19990002222', 'status': 'ONLINE'})
        params = {
            'api_url': 'https://odoo.example.com/',
            'twilio_edge': 'ashburn',
            'call_duration_limit': '3600',
        }
        with patch.object(type(self.Settings), 'get_param',
                          side_effect=lambda p, *a, **k: params.get(p, '')):
            phone = str(self.domain.originate_external_call(
                '+15559998888', {'Caller': 'client:holdowner@hold'}))
            whatsapp = str(self.domain.originate_whatsapp_call(
                '+15559998888', {'Caller': 'client:holdowner@hold'}))
        for twiml in (phone, whatsapp):
            self.assertIn(
                'action="https://odoo.example.com/twilio/webhook/callaction'
                '#e=ashburn"', twiml)

    def test_failed_redirect_clears_the_marker(self):
        """A 21220 must not leave a stored room only one leg can reach."""
        call = self._outbound_call()
        log = []
        error = TwilioRestException(
            status=400, uri='/Calls/CAOTHER',
            msg='Call is not in-progress. Cannot redirect.', code=21220)
        client = self._mock_client(log, redirect_error=error)
        with self.assertRaises(UserError) as caught:
            self._run('hold_softphone_call', log, client=client)

        self.assertIn('ended before it could be held', str(caught.exception))
        self.assertFalse(call.conference_name)
        self.assertFalse(call.conference_pending)
        commits = [e for e in log if e[0] == 'commit']
        self.assertFalse(commits[-1][2]['conference_name'])
        self.assertFalse([e for e in log if e[0] == 'participant'])

    def test_hold_and_resume_use_the_participant_api(self):
        call = self._outbound_call()
        log = []
        self._run('hold_softphone_call', log)
        held = [e for e in log if e[0] == 'participant']
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0][1], 'CAOTHER')
        self.assertTrue(held[0][2]['hold'])
        self.assertTrue(held[0][2]['hold_url'])
        self.assertTrue(call.is_on_hold)
        self.assertEqual(call.conference_sid, 'CFROOM')

        log = []
        self._run('resume_softphone_call', log)
        self.assertFalse(self._redirects(log), 'resume moves no leg')
        resumed = [e for e in log if e[0] == 'participant']
        self.assertEqual(resumed, [('participant', 'CAOTHER', {'hold': False})])
        self.assertFalse(call.is_on_hold)

    def test_refuses_a_call_that_is_not_ours(self):
        self._outbound_call()
        log = []
        with self.assertRaises(AccessError):
            self._run('hold_softphone_call', log, user=self.other_user)
        self.assertFalse(log)
