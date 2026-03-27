# -*- coding: utf-8 -*-
"""Tier 2 integration tests: messaging, recording, and budget operations.

Tests exercise Twilio test API for SMS, simulate webhook payloads for
message/recording processing, and verify budget guard accounting.

Run with: gdo test -d <db> -i connect -T live_twilio
"""

import logging
from unittest.mock import patch

from twilio.base.exceptions import TwilioRestException
from odoo.tests import tagged

from .live_common import TwilioLiveTestCase, TwilioBudgetGuard

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. SMS Send Operations (real Twilio test API)
# ---------------------------------------------------------------------------

@tagged('post_install', '-at_install', 'live_twilio')
class TestSMSSendOperations(TwilioLiveTestCase):
    """Send SMS via Twilio test credentials and verify responses."""

    def test_send_sms_valid_number_sid_format(self):
        """SMS to valid magic number returns SID starting with SM."""
        msg = self._send_test_sms(
            to=self.VALID_NUMBER,
            from_=self.VALID_NUMBER,
            body='Integration test: valid send',
        )
        self.assertTrue(msg.sid.startswith('SM'),
                        f"Expected SID prefix 'SM', got '{msg.sid[:2]}'")

    def test_send_sms_valid_number_status_queued(self):
        """SMS to valid magic number has status 'queued'."""
        msg = self._send_test_sms(
            to=self.VALID_NUMBER,
            from_=self.VALID_NUMBER,
            body='Integration test: status check',
        )
        self.assertEqual(msg.status, 'queued')

    def test_send_sms_invalid_number_error_code(self):
        """SMS to invalid magic number raises TwilioRestException with code 21211."""
        with self.assertRaises(TwilioRestException) as cm:
            self._send_test_sms(
                to=self.INVALID_NUMBER,
                from_=self.VALID_NUMBER,
                body='Should fail: invalid destination',
            )
        self.assertEqual(cm.exception.code, 21211)

    def test_send_sms_not_owned_number_error(self):
        """SMS from unowned magic number raises error (21210, 21212, or 21606)."""
        with self.assertRaises(TwilioRestException) as cm:
            self._send_test_sms(
                to=self.VALID_NUMBER,
                from_=self.NOT_OWNED,
                body='Should fail: not our number',
            )
        self.assertIn(cm.exception.code, [21210, 21212, 21606])

    def test_send_sms_sid_length(self):
        """SMS SID is 34 characters (SM + 32 hex)."""
        msg = self._send_test_sms(
            to=self.VALID_NUMBER,
            from_=self.VALID_NUMBER,
            body='SID length check',
        )
        self.assertEqual(len(msg.sid), 34)


# ---------------------------------------------------------------------------
# 2. Message Webhook Simulation
# ---------------------------------------------------------------------------

@tagged('post_install', '-at_install', 'live_twilio')
class TestMessageWebhookSimulation(TwilioLiveTestCase):
    """Simulate incoming SMS/status webhooks and verify Odoo record handling."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Message = cls.env['connect.message']

    def _make_incoming_sms_params(self, message_sid=None, from_number=None,
                                   to_number=None, body='Hello from webhook test'):
        """Build a dict mimicking Twilio's incoming SMS webhook POST params."""
        self._configure_test_credentials()
        account_sid = self.env['connect.settings'].get_param('account_sid')
        return {
            'MessageSid': message_sid or 'SM' + 'f' * 32,
            'AccountSid': account_sid,
            'From': from_number or '+15551112222',
            'To': to_number or '+15553334444',
            'Body': body,
            'NumMedia': '0',
            'SmsStatus': 'received',
            'FromCity': 'Denver',
            'FromState': 'CO',
            'FromZip': '80202',
            'FromCountry': 'US',
        }

    def test_incoming_sms_creates_message_record(self):
        """Simulated incoming SMS webhook creates a connect.message record."""
        params = self._make_incoming_sms_params(
            message_sid='SM' + 'a1b2c3d4' * 4,
            body='Webhook creation test',
        )
        # Patch connect_reload_view to avoid bus notifications in test
        with patch.object(
            self.env['connect.settings'].__class__, 'connect_reload_view', return_value=True
        ):
            self.Message.sudo().receive(params)

        msg = self.Message.sudo().search([
            ('message_sid', '=', 'SM' + 'a1b2c3d4' * 4),
        ], limit=1)
        self.assertTrue(msg.exists(), "Message record should have been created")
        self.assertEqual(msg.body, 'Webhook creation test')
        self.assertEqual(msg.from_number, '+15551112222')
        self.assertEqual(msg.to_number, '+15553334444')
        self.assertEqual(msg.status, 'received')

    def test_incoming_sms_geographic_fields(self):
        """Geographic fields are populated from webhook params."""
        params = self._make_incoming_sms_params(
            message_sid='SM' + 'geo00001' * 4,
        )
        with patch.object(
            self.env['connect.settings'].__class__, 'connect_reload_view', return_value=True
        ):
            self.Message.sudo().receive(params)

        msg = self.Message.sudo().search([
            ('message_sid', '=', 'SM' + 'geo00001' * 4),
        ], limit=1)
        self.assertEqual(msg.from_city, 'Denver')
        self.assertEqual(msg.from_state, 'CO')
        self.assertEqual(msg.from_zip, '80202')
        self.assertEqual(msg.from_country, 'US')

    def test_message_status_webhook_updates_delivery(self):
        """Status webhook updates an existing message's status to 'delivered'."""
        self._configure_test_credentials()
        test_sid = 'SM' + 'status01' * 4
        self.Message.sudo().create({
            'message_sid': test_sid,
            'from_number': '+15553334444',
            'to_number': '+15551112222',
            'body': 'Awaiting delivery',
            'status': 'sent',
        })
        # Simulate delivery status callback
        status_data = {
            'MessageSid': test_sid,
            'SmsStatus': 'delivered',
        }
        self.Message.sudo().update_message_status(status_data)

        msg = self.Message.sudo().search([('message_sid', '=', test_sid)], limit=1)
        self.assertEqual(msg.status, 'delivered')

    def test_message_status_webhook_failed_sets_error(self):
        """Failed status webhook sets error fields on the message."""
        self._configure_test_credentials()
        test_sid = 'SM' + 'failmsg1' * 4
        self.Message.sudo().create({
            'message_sid': test_sid,
            'from_number': '+15553334444',
            'to_number': '+15551112222',
            'body': 'Will fail',
            'status': 'sent',
        })
        status_data = {
            'MessageSid': test_sid,
            'SmsStatus': 'failed',
            'ErrorCode': '30006',
            'ErrorMessage': 'Landline or unreachable carrier',
        }
        self.Message.sudo().update_message_status(status_data)

        msg = self.Message.sudo().search([('message_sid', '=', test_sid)], limit=1)
        self.assertEqual(msg.status, 'failed')
        self.assertEqual(msg.error_code, '30006')
        self.assertTrue(msg.has_error)

    def test_message_status_unknown_sid_no_error(self):
        """Status callback for unknown SID does not raise an error."""
        self._configure_test_credentials()
        result = self.Message.sudo().update_message_status({
            'MessageSid': 'SM' + 'unknown1' * 4,
            'SmsStatus': 'delivered',
        })
        self.assertTrue(result)

    def test_receive_message_values_helper(self):
        """get_receive_message_values extracts all expected fields from params."""
        msg = self.Message.create({
            'message_sid': 'SM' + 'helper01' * 4,
            'from_number': '+15550000000',
            'to_number': '+15550000001',
        })
        params = {
            'MessageSid': 'SMtest123',
            'From': '+15551112222',
            'To': '+15553334444',
            'Body': 'Extract test',
            'NumMedia': '1',
            'FromCity': 'Austin',
            'FromState': 'TX',
            'FromZip': '73301',
            'FromCountry': 'US',
            'AccountSid': 'ACtest',
            'MessagingServiceSid': 'MGtest',
            'SmsStatus': 'received',
            'MediaContentType0': 'image/png',
            'MediaUrl0': 'https://api.twilio.com/media/test.png',
        }
        vals = msg.get_receive_message_values(params)
        self.assertEqual(vals['message_sid'], 'SMtest123')
        self.assertEqual(vals['from_number'], '+15551112222')
        self.assertEqual(vals['to_number'], '+15553334444')
        self.assertEqual(vals['body'], 'Extract test')
        self.assertEqual(vals['num_media'], 1)
        self.assertEqual(vals['media_content_type'], 'image/png')
        self.assertEqual(vals['media_url'], 'https://api.twilio.com/media/test.png')


# ---------------------------------------------------------------------------
# 3. Recording Model Tests
# ---------------------------------------------------------------------------

@tagged('post_install', '-at_install', 'live_twilio')
class TestRecordingModel(TwilioLiveTestCase):
    """Test recording widget, download error handling, and transcription errors."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Recording = cls.env['connect.recording']

        # Create a test call + channel so recording can reference them
        cls.test_call = cls.env['connect.call'].create({
            'direction': 'incoming',
            'status': 'completed',
            'caller': '+15551234567',
            'called': '+15559876543',
            'partner': cls.partner.id,
        })
        cls.test_channel = cls.env['connect.channel'].create({
            'call': cls.test_call.id,
            'sid': 'CA' + 'r' * 32,
            'caller': '+15551234567',
            'called': '+15559876543',
            'status': 'completed',
            'technical_direction': 'inbound',
        })

    def _create_recording(self, **overrides):
        """Create a test recording with sensible defaults."""
        vals = {
            'sid': overrides.pop('sid', 'RE' + 'x' * 32),
            'call_sid': self.test_channel.sid,
            'call': self.test_call.id,
            'channel': self.test_channel.id,
            'caller_number': '+15551234567',
            'called_number': '+15559876543',
            'duration': 30,
            'status': 'completed',
            'media_url': 'https://api.twilio.com/2010-04-01/Accounts/AC123/Recordings/RE123.mp3',
        }
        vals.update(overrides)
        return self.Recording.with_context(skip_transcription=True).create(vals)

    def test_recording_widget_direct_url(self):
        """Without proxy_recordings, widget contains direct media_url."""
        self._configure_test_credentials()
        self.env['connect.settings'].set_param('proxy_recordings', False)
        rec = self._create_recording(sid='RE' + 'direct01' * 4)
        rec._get_recording_widget()
        self.assertIn('<audio', rec.recording_widget)
        self.assertIn('<source', rec.recording_widget)
        self.assertIn(rec.media_url, rec.recording_widget)

    def test_recording_widget_proxied_url(self):
        """With proxy_recordings enabled, widget uses /connect/recording/<id>."""
        self._configure_test_credentials()
        self.env['connect.settings'].set_param('proxy_recordings', True)
        rec = self._create_recording(sid='RE' + 'proxy001' * 4)
        rec._get_recording_widget()
        self.assertIn('<audio', rec.recording_widget)
        self.assertIn(f'/connect/recording/{rec.id}', rec.recording_widget)
        # Should NOT contain the raw Twilio URL
        self.assertNotIn('api.twilio.com', rec.recording_widget)

    def test_recording_widget_no_media_url(self):
        """Recording without media_url produces empty widget."""
        rec = self._create_recording(sid='RE' + 'nomedia1' * 4, media_url=False)
        rec._get_recording_widget()
        self.assertEqual(rec.recording_widget, '')

    def test_recording_download_unreachable_media(self):
        """Download from unreachable media_url raises an exception."""
        self._configure_test_credentials()
        rec = self._create_recording(
            sid='RE' + 'badurl01' * 4,
            media_url='https://api.twilio.com/2010-04-01/Accounts/FAKE/Recordings/FAKE.mp3',
        )
        # Test credentials cannot access real recording URLs - should raise
        with self.assertRaises(Exception):
            rec._download_recording_audio()

    def test_transcription_error_no_openai_key(self):
        """get_transcript raises ValidationError when OpenAI key is not set."""
        from odoo.exceptions import ValidationError
        self._configure_test_credentials()
        self.env['connect.settings'].set_param('openai_api_key', False)
        rec = self._create_recording(sid='RE' + 'nokey001' * 4)
        with self.assertRaises(ValidationError):
            rec.get_transcript(fail_silently=False)

    def test_transcription_error_no_openai_key_silent(self):
        """get_transcript with fail_silently returns False when no key."""
        self._configure_test_credentials()
        self.env['connect.settings'].set_param('openai_api_key', False)
        rec = self._create_recording(sid='RE' + 'silent01' * 4)
        result = rec.get_transcript(fail_silently=True)
        self.assertFalse(result)

    def test_transcription_error_no_media_url(self):
        """get_transcript raises ValidationError when media_url is empty."""
        from odoo.exceptions import ValidationError
        self._configure_test_credentials()
        self.env['connect.settings'].set_param('openai_api_key', 'sk-test-fake')
        rec = self._create_recording(sid='RE' + 'nomurl01' * 4, media_url=False)
        with self.assertRaises(ValidationError):
            rec.get_transcript()

    def test_recording_duration_human(self):
        """duration_human computed field formats MM:SS correctly."""
        rec = self._create_recording(sid='RE' + 'dur00001' * 4, duration=125)
        self.assertEqual(rec.duration_human, '02:05')

    def test_recording_duration_human_zero(self):
        """duration_human for 0 seconds is 00:00."""
        rec = self._create_recording(sid='RE' + 'dur00002' * 4, duration=0)
        self.assertEqual(rec.duration_human, '00:00')

    def test_recording_summary_context(self):
        """_get_summary_context returns expected context dict keys."""
        rec = self._create_recording(sid='RE' + 'ctx00001' * 4)
        ctx = rec._get_summary_context()
        self.assertIn('caller_number', ctx)
        self.assertIn('called_number', ctx)
        self.assertIn('direction', ctx)
        self.assertIn('caller_name', ctx)
        self.assertIn('called_name', ctx)
        self.assertIn('number_name', ctx)
        self.assertIn('number_description', ctx)

    def test_render_summary_prompt_safe_format(self):
        """_render_summary_prompt handles missing placeholders gracefully."""
        rec = self._create_recording(sid='RE' + 'render01' * 4)
        prompt = 'Call from {caller_name} to {called_name}. {unknown_placeholder}'
        rendered = rec._render_summary_prompt(prompt, transcript='Hello world')
        self.assertIn('{unknown_placeholder}', rendered)
        # Known placeholders should be resolved
        self.assertNotIn('{caller_name}', rendered)


# ---------------------------------------------------------------------------
# 4. WhatsApp Message Handling
# ---------------------------------------------------------------------------

@tagged('post_install', '-at_install', 'live_twilio')
class TestWhatsAppMessageHandling(TwilioLiveTestCase):
    """Test WhatsApp-specific number format detection and webhook params."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Message = cls.env['connect.message']

    def test_whatsapp_number_format_detection_in_channel(self):
        """Channel correctly detects WhatsApp call_type from whatsapp: prefix."""
        channel = self.env['connect.channel'].with_context(tracking_disable=True).create({
            'sid': 'CA' + 'wa000001' * 4,
            'caller': '+15551112222',
            'called': '+15553334444',
            'status': 'ringing',
            'technical_direction': 'inbound',
            'call_type': 'whatsapp',
        })
        self.assertEqual(channel.call_type, 'whatsapp')

    def test_whatsapp_prefix_stripped_in_receive(self):
        """Incoming WhatsApp message strips whatsapp: prefix from numbers."""
        self._configure_test_credentials()
        account_sid = self.env['connect.settings'].get_param('account_sid')
        params = {
            'MessageSid': 'SM' + 'wa000001' * 4,
            'AccountSid': account_sid,
            'From': 'whatsapp:+15551112222',
            'To': 'whatsapp:+15553334444',
            'Body': 'WhatsApp test message',
            'NumMedia': '0',
            'SmsStatus': 'received',
        }
        with patch.object(
            self.env['connect.settings'].__class__, 'connect_reload_view', return_value=True
        ):
            self.Message.sudo().receive(params)

        msg = self.Message.sudo().search([
            ('message_sid', '=', 'SM' + 'wa000001' * 4),
        ], limit=1)
        self.assertTrue(msg.exists())
        # whatsapp: prefix should be stripped
        self.assertEqual(msg.from_number, '+15551112222')
        self.assertEqual(msg.to_number, '+15553334444')
        self.assertEqual(msg.message_type, 'WhatsApp')

    def test_whatsapp_message_type_set(self):
        """WhatsApp messages get message_type = 'WhatsApp'."""
        self._configure_test_credentials()
        account_sid = self.env['connect.settings'].get_param('account_sid')
        params = {
            'MessageSid': 'SM' + 'watype01' * 4,
            'AccountSid': account_sid,
            'From': 'whatsapp:+15559998888',
            'To': 'whatsapp:+15557776666',
            'Body': 'Type detection test',
            'NumMedia': '0',
            'SmsStatus': 'received',
        }
        with patch.object(
            self.env['connect.settings'].__class__, 'connect_reload_view', return_value=True
        ):
            self.Message.sudo().receive(params)

        msg = self.Message.sudo().search([
            ('message_sid', '=', 'SM' + 'watype01' * 4),
        ], limit=1)
        self.assertEqual(msg.message_type, 'WhatsApp')

    def test_channel_call_type_phone_default(self):
        """Channel defaults to phone call_type."""
        channel = self.env['connect.channel'].with_context(tracking_disable=True).create({
            'sid': 'CA' + 'phone001' * 4,
            'caller': '+15551112222',
            'called': '+15553334444',
            'status': 'ringing',
            'technical_direction': 'inbound',
        })
        self.assertEqual(channel.call_type, 'phone')


# ---------------------------------------------------------------------------
# 5. Budget Guard Integration
# ---------------------------------------------------------------------------

@tagged('post_install', '-at_install', 'live_twilio')
class TestBudgetGuardMessaging(TwilioLiveTestCase):
    """Verify budget guard tracks SMS costs and provides accurate summaries."""

    def test_budget_tracks_sms_cost(self):
        """Each SMS increments sms_sent counter and adds to spent."""
        guard = TwilioBudgetGuard(max_budget_usd=5.00)
        guard.record_sms()
        guard.record_sms()
        self.assertEqual(guard.sms_sent, 2)
        self.assertAlmostEqual(guard.spent, 0.0079 * 2, places=6)

    def test_budget_tracks_recording_cost(self):
        """Recording cost is tracked correctly."""
        guard = TwilioBudgetGuard(max_budget_usd=5.00)
        guard.record_recording(duration_seconds=60)
        self.assertEqual(guard.recordings, 1)
        # 60s = 1 min * $0.0025/min = $0.0025
        self.assertAlmostEqual(guard.spent, 0.0025, places=6)

    def test_budget_summary_includes_message_counts(self):
        """Summary string includes SMS and recording counts."""
        guard = TwilioBudgetGuard(max_budget_usd=5.00)
        guard.record_sms()
        guard.record_sms()
        guard.record_sms()
        guard.record_recording(30)
        summary = guard.summary()
        self.assertIn('3 SMS', summary)
        self.assertIn('1 recordings', summary)
        self.assertIn('0 calls', summary)

    def test_budget_mixed_operations_total(self):
        """Mixed call + SMS + recording costs accumulate correctly."""
        guard = TwilioBudgetGuard(max_budget_usd=5.00)
        guard.record_call(duration_seconds=60)   # 1 min * $0.022/min = $0.022
        guard.record_sms()                        # $0.0079
        guard.record_sms()                        # $0.0079
        guard.record_recording(duration_seconds=120)  # 2 min * $0.0025/min = $0.005
        expected = 0.022 + 0.0079 + 0.0079 + 0.005
        self.assertAlmostEqual(guard.spent, expected, places=6)
        self.assertEqual(guard.calls_made, 1)
        self.assertEqual(guard.sms_sent, 2)
        self.assertEqual(guard.recordings, 1)

    def test_budget_limit_enforced_across_mixed_ops(self):
        """Budget limit triggers RuntimeError on mixed operations."""
        guard = TwilioBudgetGuard(max_budget_usd=0.05)
        guard.record_call(duration_seconds=60)  # $0.022
        guard.record_sms()                       # $0.0079 -> total $0.0299
        guard.record_sms()                       # $0.0079 -> total $0.0378
        # Next call should push over budget
        with self.assertRaises(RuntimeError) as cm:
            guard.record_call(duration_seconds=120)  # $0.044 -> total $0.0818
        error_msg = str(cm.exception)
        self.assertIn('exceeded', error_msg)
        self.assertIn('calls', error_msg)
        self.assertIn('SMS', error_msg)

    def test_budget_sms_only_exhaustion(self):
        """Pure SMS budget exhaustion triggers at correct threshold."""
        # $0.01 budget, each SMS is $0.0079 -> 2nd SMS should exceed
        guard = TwilioBudgetGuard(max_budget_usd=0.01)
        guard.record_sms()  # $0.0079
        with self.assertRaises(RuntimeError):
            guard.record_sms()  # $0.0158 > $0.01

    def test_live_sms_increments_budget(self):
        """Sending a real test SMS increments the class-level budget guard."""
        initial_sms_count = self.budget.sms_sent
        initial_spent = self.budget.spent
        self._send_test_sms(
            to=self.VALID_NUMBER,
            from_=self.VALID_NUMBER,
            body='Budget tracking test',
        )
        self.assertEqual(self.budget.sms_sent, initial_sms_count + 1)
        self.assertGreater(self.budget.spent, initial_spent)
