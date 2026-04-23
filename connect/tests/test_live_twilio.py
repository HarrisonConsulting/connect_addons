# -*- coding: utf-8 -*-
"""Tier 2 integration tests: real Twilio test API, zero cost.

These tests exercise the actual Twilio REST API using test credentials
and magic phone numbers. No real calls are made, no charges incurred.

Run with: gdo test -d <db> -i connect -T live_twilio
Skipped by default in normal test runs.
"""

import logging

from twilio.base.exceptions import TwilioRestException
from odoo.tests import tagged

from .live_common import TwilioLiveTestCase, MAGIC_NUMBERS

logger = logging.getLogger(__name__)


@tagged('post_install', '-at_install', 'live_twilio')
class TestTwilioCallAPI(TwilioLiveTestCase):
    """Test call creation via Twilio test API with magic numbers."""

    def test_create_call_valid_number(self):
        """Call to valid magic number returns queued status."""
        call = self._make_test_call(
            to=self.VALID_NUMBER,
            from_=self.VALID_NUMBER,
        )
        self.assertTrue(call.sid.startswith('CA'))
        self.assertEqual(call.status, 'queued')

    def test_create_call_invalid_number(self):
        """Call to invalid magic number raises TwilioRestException."""
        with self.assertRaises(TwilioRestException) as cm:
            self._make_test_call(
                to=self.INVALID_NUMBER,
                from_=self.VALID_NUMBER,
            )
        self.assertEqual(cm.exception.code, 21217)

    def test_create_call_cant_route(self):
        """Call to unroutable magic number raises TwilioRestException."""
        with self.assertRaises(TwilioRestException) as cm:
            self._make_test_call(
                to=self.CANT_ROUTE,
                from_=self.VALID_NUMBER,
            )
        self.assertEqual(cm.exception.code, 21214)

    def test_create_call_not_owned_number(self):
        """Call from number not owned by account raises error."""
        with self.assertRaises(TwilioRestException) as cm:
            self._make_test_call(
                to=self.VALID_NUMBER,
                from_=self.NOT_OWNED,
            )
        self.assertIn(cm.exception.code, [21210, 21212])

    def test_create_call_with_twiml(self):
        """Call with custom TwiML returns valid response."""
        twiml = '<Response><Say voice="alice">Hello from Connect test</Say><Hangup/></Response>'
        call = self._make_test_call(twiml=twiml)
        self.assertTrue(call.sid.startswith('CA'))
        self.assertEqual(call.status, 'queued')

    def test_create_call_with_gather_twiml(self):
        """Call with Gather (IVR) TwiML is accepted."""
        twiml = '''<Response>
            <Gather input="dtmf" numDigits="1" timeout="5">
                <Say>Press 1 for sales, 2 for support</Say>
            </Gather>
            <Say>We didn't receive any input. Goodbye!</Say>
        </Response>'''
        call = self._make_test_call(twiml=twiml)
        self.assertTrue(call.sid)

    def test_create_call_with_dial_twiml(self):
        """Call with Dial (transfer) TwiML is accepted."""
        twiml = '''<Response>
            <Dial timeout="30" record="record-from-answer-dual">
                <Number>+15005550006</Number>
            </Dial>
        </Response>'''
        call = self._make_test_call(twiml=twiml)
        self.assertTrue(call.sid)

    def test_create_call_with_conference_twiml(self):
        """Call with Conference (parking/bridge) TwiML is accepted."""
        twiml = '''<Response>
            <Dial>
                <Conference waitUrl="http://twimlets.com/holdmusic?Bucket=com.twilio.music.classical"
                            startConferenceOnEnter="true"
                            endConferenceOnExit="false">
                    TestParkSlot1
                </Conference>
            </Dial>
        </Response>'''
        call = self._make_test_call(twiml=twiml)
        self.assertTrue(call.sid)

    def test_create_call_with_record_twiml(self):
        """Call with Record (voicemail) TwiML is accepted."""
        twiml = '''<Response>
            <Say>Please leave a message after the beep.</Say>
            <Record maxLength="120" finishOnKey="#"
                    recordingStatusCallback="https://example.com/recording"/>
        </Response>'''
        call = self._make_test_call(twiml=twiml)
        self.assertTrue(call.sid)


@tagged('post_install', '-at_install', 'live_twilio')
class TestTwilioSMSAPI(TwilioLiveTestCase):
    """Test SMS/message creation via Twilio test API."""

    def test_send_sms_valid(self):
        """SMS to valid magic number succeeds."""
        msg = self._send_test_sms(
            to=self.VALID_NUMBER,
            from_=self.VALID_NUMBER,
            body='Connect test SMS',
        )
        self.assertTrue(msg.sid.startswith('SM'))
        self.assertEqual(msg.status, 'queued')

    def test_send_sms_invalid_to(self):
        """SMS to invalid magic number raises error."""
        with self.assertRaises(TwilioRestException) as cm:
            self._send_test_sms(
                to=self.INVALID_NUMBER,
                from_=self.VALID_NUMBER,
            )
        self.assertEqual(cm.exception.code, 21211)

    def test_send_sms_not_owned_from(self):
        """SMS from number not owned raises error."""
        with self.assertRaises(TwilioRestException) as cm:
            self._send_test_sms(
                to=self.VALID_NUMBER,
                from_=self.NOT_OWNED,
            )
        self.assertIn(cm.exception.code, [21210, 21212, 21606])


@tagged('post_install', '-at_install', 'live_twilio')
class TestTwilioAccountAPI(TwilioLiveTestCase):
    """Test account-level operations via test API."""

    def test_client_credentials_valid(self):
        """Test credentials authenticate successfully (call creation proves it)."""
        call = self._make_test_call()
        self.assertTrue(call.sid.startswith('CA'))

    def test_client_has_region_attribute(self):
        """Client exposes region attribute (None until explicitly configured)."""
        self.assertTrue(hasattr(self.twilio_client, 'region'))


@tagged('post_install', '-at_install', 'live_twilio')
class TestConnectOdooIntegration(TwilioLiveTestCase):
    """Test Connect Odoo models with real Twilio test credentials configured."""

    def test_get_client_with_test_creds(self):
        """get_client returns valid Twilio client when test creds configured."""
        self._configure_test_credentials()
        client = self.env['connect.settings'].get_client(region=False)
        self.assertIsNotNone(client)

    def test_originate_call_valid(self):
        """Call origination through Connect models with test credentials."""
        self._configure_test_credentials()
        client = self.env['connect.settings'].get_client(region=False)
        self.assertIsNotNone(client)

        call = client.calls.create(
            to=self.VALID_NUMBER,
            from_=self.VALID_NUMBER,
            twiml='<Response><Say>Test origination</Say></Response>',
        )
        self.budget.record_call()
        self.assertTrue(call.sid.startswith('CA'))

    def test_originate_call_invalid_returns_error(self):
        """Call to invalid number returns proper Twilio error."""
        self._configure_test_credentials()
        client = self.env['connect.settings'].get_client(region=False)

        with self.assertRaises(TwilioRestException) as cm:
            client.calls.create(
                to=self.INVALID_NUMBER,
                from_=self.VALID_NUMBER,
                twiml='<Response><Say>Should fail</Say></Response>',
            )
        self.assertIn(cm.exception.code, [21211, 21214, 21217])


@tagged('post_install', '-at_install', 'live_twilio')
class TestConnectCallFlowTwiML(TwilioLiveTestCase):
    """Test that Connect callflow TwiML generation produces valid TwiML
    that Twilio's test API accepts."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Audio = cls.env['connect.audio']
        cls.prompt_audio = Audio.create({
            'name': 'Live twilio prompt',
            'source': 'twilio_tts',
            'static_text': 'Press 1 for sales, press 2 for support.',
        })
        cls.invalid_audio = Audio.create({
            'name': 'Live twilio invalid',
            'source': 'twilio_tts',
            'static_text': 'Invalid selection.',
        })
        cls.callflow = cls.env['connect.callflow'].create({
            'name': 'Test IVR',
            'gather_input': True,
            'gather_input_type': 'dtmf',
            'gather_digits': 1,
            'gather_timeout': 5,
            'prompt_audio_id': cls.prompt_audio.id,
            'invalid_input_audio_id': cls.invalid_audio.id,
        })

    def test_callflow_twiml_accepted_by_twilio(self):
        """TwiML generated by a callflow is syntactically valid for Twilio."""
        self._configure_test_credentials()
        twiml_response = self.callflow.render()
        twiml = str(twiml_response)
        self.assertIn('<Gather', twiml)
        self.assertIn('numDigits="1"', twiml)

        call = self.twilio_client.calls.create(
            to=self.VALID_NUMBER,
            from_=self.VALID_NUMBER,
            twiml=twiml,
        )
        self.budget.record_call()
        self.assertTrue(call.sid.startswith('CA'),
                        f"Twilio rejected our TwiML: {twiml[:200]}")

    def test_after_hours_twiml_accepted(self):
        """After-hours TwiML is syntactically valid for Twilio."""
        self._configure_test_credentials()
        self.callflow.business_hours_enabled = True
        self.callflow.after_hours_audio_id = self.env['connect.audio'].create({
            'name': 'Live twilio after-hours',
            'source': 'twilio_tts',
            'static_text': 'We are currently closed.',
        })

        twiml_response = self.callflow._render_after_hours()
        twiml = str(twiml_response)
        self.assertIn('<Say', twiml)

        call = self.twilio_client.calls.create(
            to=self.VALID_NUMBER,
            from_=self.VALID_NUMBER,
            twiml=twiml,
        )
        self.budget.record_call()
        self.assertTrue(call.sid.startswith('CA'))

    def test_voicemail_twiml_accepted(self):
        """Voicemail TwiML (Record + Say) is syntactically valid for Twilio."""
        self._configure_test_credentials()
        twiml = '''<Response>
            <Say>Please leave a message after the beep.</Say>
            <Record maxLength="120" finishOnKey="#"/>
        </Response>'''
        call = self.twilio_client.calls.create(
            to=self.VALID_NUMBER,
            from_=self.VALID_NUMBER,
            twiml=twiml,
        )
        self.budget.record_call()
        self.assertTrue(call.sid.startswith('CA'))


@tagged('post_install', '-at_install', 'live_twilio')
class TestConnectCallQueueTwiML(TwilioLiveTestCase):
    """Test that queue-related TwiML works with Twilio test API."""

    def test_queue_hold_music_twiml(self):
        """Queue hold music TwiML (Enqueue + waitUrl) is accepted by Twilio."""
        self._configure_test_credentials()
        twiml = '''<Response>
            <Enqueue waitUrl="/twilio/webhook/queue/1/render_wait_app"
                     action="/twilio/webhook/queue/1/on_action">
                TestQueue
            </Enqueue>
        </Response>'''
        call = self.twilio_client.calls.create(
            to=self.VALID_NUMBER,
            from_=self.VALID_NUMBER,
            twiml=twiml,
        )
        self.budget.record_call()
        self.assertTrue(call.sid.startswith('CA'))

    def test_conference_bridge_twiml(self):
        """Conference bridge TwiML (for call parking) accepted by Twilio."""
        self._configure_test_credentials()
        twiml = '''<Response>
            <Dial>
                <Conference startConferenceOnEnter="true"
                            endConferenceOnExit="false"
                            waitUrl="http://twimlets.com/holdmusic?Bucket=com.twilio.music.classical">
                    ParkSlot1
                </Conference>
            </Dial>
        </Response>'''
        call = self.twilio_client.calls.create(
            to=self.VALID_NUMBER,
            from_=self.VALID_NUMBER,
            twiml=twiml,
        )
        self.budget.record_call()
        self.assertTrue(call.sid.startswith('CA'))

    def test_transfer_blind_twiml(self):
        """Blind transfer TwiML (Dial + Number) accepted by Twilio."""
        self._configure_test_credentials()
        twiml = '''<Response>
            <Dial timeout="30">
                <Number>+15005550006</Number>
            </Dial>
        </Response>'''
        call = self.twilio_client.calls.create(
            to=self.VALID_NUMBER,
            from_=self.VALID_NUMBER,
            twiml=twiml,
        )
        self.budget.record_call()
        self.assertTrue(call.sid.startswith('CA'))

    def test_transfer_attended_twiml(self):
        """Attended transfer TwiML (Dial + Client) accepted by Twilio."""
        self._configure_test_credentials()
        twiml = '''<Response>
            <Dial timeout="30" record="record-from-answer-dual">
                <Client>
                    <Identity>agent1</Identity>
                </Client>
            </Dial>
        </Response>'''
        call = self.twilio_client.calls.create(
            to=self.VALID_NUMBER,
            from_=self.VALID_NUMBER,
            twiml=twiml,
        )
        self.budget.record_call()
        self.assertTrue(call.sid.startswith('CA'))


@tagged('post_install', '-at_install', 'live_twilio')
class TestBudgetGuard(TwilioLiveTestCase):
    """Test that the budget guard mechanism works correctly."""

    def test_budget_tracks_calls(self):
        """Budget guard tracks call costs correctly."""
        guard = self.__class__.budget.__class__(max_budget_usd=0.10)
        guard.record_call(duration_seconds=60)
        self.assertEqual(guard.calls_made, 1)
        self.assertGreater(guard.spent, 0)

    def test_budget_raises_on_exceeded(self):
        """Budget guard raises RuntimeError when budget exceeded."""
        guard = self.__class__.budget.__class__(max_budget_usd=0.01)
        with self.assertRaises(RuntimeError) as cm:
            for _ in range(100):
                guard.record_call(duration_seconds=60)
        self.assertIn('exceeded', str(cm.exception))

    def test_budget_summary(self):
        """Budget guard produces readable summary."""
        guard = self.__class__.budget.__class__(max_budget_usd=5.00)
        guard.record_call(30)
        guard.record_sms()
        summary = guard.summary()
        self.assertIn('1 calls', summary)
        self.assertIn('1 SMS', summary)
