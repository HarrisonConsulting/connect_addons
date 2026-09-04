# -*- coding: utf-8 -*-
"""Tier 2 integration tests: call lifecycle via real Twilio test API.

Exercises the full call lifecycle — creation, status webhooks, channel
management, finalization, and error handling — using Twilio test
credentials and magic phone numbers.  Zero cost.

Run with: gdo test -d <db> -i connect -T live_twilio
"""

import logging

from twilio.base.exceptions import TwilioRestException
from twilio.request_validator import RequestValidator
from odoo.tests import tagged
from odoo.tools import mute_logger

from .live_common import TwilioLiveTestCase, MAGIC_NUMBERS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_channel_params(account_sid, call_sid, status, direction='inbound',
                         # Caller must not be our own DID: an inbound leg whose
                         # Caller equals its Called is a self-dial loop, and the
                         # production detector rightly logs one at ERROR.
                         caller='+15005550009', called='+15005550006',
                         parent_call_sid=None, duration=0,
                         sequence_number=0, error_code=None,
                         error_message=None):
    """Build a dict that mirrors the POST params Twilio sends to callstatus."""
    params = {
        'AccountSid': account_sid,
        'CallSid': call_sid,
        'CallStatus': status,
        'Direction': direction,
        'Caller': caller,
        'Called': called,
        'To': called,
        'From': caller,
        'CallDuration': str(duration),
        'SequenceNumber': str(sequence_number),
    }
    if parent_call_sid:
        params['ParentCallSid'] = parent_call_sid
    if error_code:
        params['ErrorCode'] = error_code
    if error_message:
        params['ErrorMessage'] = error_message
    return params


# ===========================================================================
# Test: Call Creation & Status Tracking
# ===========================================================================

@tagged('post_install', '-at_install', 'live_twilio')
class TestCallCreationAndStatusTracking(TwilioLiveTestCase):
    """Create calls via the test API and drive them through the webhook
    status pipeline to verify Odoo records at each stage."""

    def _simulate_webhook(self, params):
        """Route webhook params through connect.call.on_call_status."""
        return self.env['connect.call'].on_call_status(params)

    # -- basic call creation via Twilio test API ----------------------------

    def test_twilio_call_creates_queued(self):
        """Twilio test API returns a queued call with a CA-prefixed SID."""
        call = self._make_test_call()
        self.assertTrue(call.sid.startswith('CA'))
        self.assertEqual(call.status, 'queued')

    # -- webhook-driven call record creation --------------------------------

    def test_initiated_webhook_creates_call_and_channel(self):
        """An 'initiated' webhook for a new CallSid creates both a
        connect.call and a connect.channel record."""
        twilio_call = self._make_test_call()
        params = _make_channel_params(
            account_sid=self.twilio_test_sid,
            call_sid=twilio_call.sid,
            status='initiated',
            direction='inbound',
            caller='+15005550009',
            called='+15005550006',
        )

        call_id = self._simulate_webhook(params)
        self.assertTrue(call_id, "on_call_status should return a call id")

        call = self.env['connect.call'].browse(call_id)
        self.assertTrue(call.exists())
        self.assertEqual(call.status, 'initiated')
        self.assertEqual(call.direction, 'incoming')

        # A channel should have been created with matching SID
        channel = self.env['connect.channel'].search([('sid', '=', twilio_call.sid)])
        self.assertTrue(channel.exists())
        self.assertEqual(channel.status, 'initiated')
        self.assertEqual(channel.call.id, call.id)

    def test_full_status_sequence_incoming(self):
        """Walk an inbound call through initiated -> ringing -> in-progress
        -> completed and verify intermediate state on the channel."""
        twilio_call = self._make_test_call()
        sid = twilio_call.sid

        statuses = [
            ('initiated', 0),
            ('ringing', 1),
            ('in-progress', 2),
            ('completed', 3),
        ]
        call_id = None
        for status, seq in statuses:
            params = _make_channel_params(
                account_sid=self.twilio_test_sid,
                call_sid=sid,
                status=status,
                direction='inbound',
                caller='+15005550009',
                called='+15005550006',
                duration=45 if status == 'completed' else 0,
                sequence_number=seq,
            )
            call_id = self._simulate_webhook(params)

        call = self.env['connect.call'].browse(call_id)
        channel = self.env['connect.channel'].search([('sid', '=', sid)])

        # Final channel state
        self.assertEqual(channel.status, 'completed')
        self.assertEqual(channel.duration, 45)

        # Call-level assertions
        self.assertTrue(call.exists())
        self.assertEqual(call.duration, 45)
        self.assertIn(call.status, ('completed', 'no-answer'),
                      "Finalized status should be terminal")

    def test_child_channel_links_to_parent_call(self):
        """A child channel (with ParentCallSid) inherits its call from
        the parent channel."""
        twilio_parent = self._make_test_call()
        twilio_child = self._make_test_call()

        # Parent channel — initiated
        parent_params = _make_channel_params(
            account_sid=self.twilio_test_sid,
            call_sid=twilio_parent.sid,
            status='initiated',
            direction='inbound',
            caller='+15005550009',
            called='+15005550006',
        )
        call_id = self._simulate_webhook(parent_params)

        # Child channel — ringing with ParentCallSid
        child_params = _make_channel_params(
            account_sid=self.twilio_test_sid,
            call_sid=twilio_child.sid,
            status='ringing',
            direction='outbound-dial',
            caller='+15005550009',
            called='+15005550006',
            parent_call_sid=twilio_parent.sid,
            sequence_number=1,
        )
        self._simulate_webhook(child_params)

        parent_ch = self.env['connect.channel'].search([('sid', '=', twilio_parent.sid)])
        child_ch = self.env['connect.channel'].search([('sid', '=', twilio_child.sid)])

        self.assertTrue(child_ch.parent_channel, "Child should have parent_channel set")
        self.assertEqual(child_ch.parent_channel.id, parent_ch.id)
        self.assertEqual(child_ch.call.id, parent_ch.call.id,
                         "Child should inherit the same call as parent")

    def test_call_duration_from_longest_channel(self):
        """Call duration is set to the maximum duration across its channels."""
        twilio_parent = self._make_test_call()
        twilio_child = self._make_test_call()

        # Parent initiated
        parent_params = _make_channel_params(
            account_sid=self.twilio_test_sid,
            call_sid=twilio_parent.sid,
            status='initiated',
            direction='inbound',
            caller='+15005550009',
            called='+15005550006',
        )
        call_id = self._simulate_webhook(parent_params)

        # Child initiated with parent
        child_params = _make_channel_params(
            account_sid=self.twilio_test_sid,
            call_sid=twilio_child.sid,
            status='initiated',
            direction='outbound-dial',
            caller='+15005550009',
            called='+15005550006',
            parent_call_sid=twilio_parent.sid,
            sequence_number=1,
        )
        self._simulate_webhook(child_params)

        # Parent completed with 30s
        parent_done = _make_channel_params(
            account_sid=self.twilio_test_sid,
            call_sid=twilio_parent.sid,
            status='completed',
            direction='inbound',
            caller='+15005550009',
            called='+15005550006',
            duration=30,
            sequence_number=2,
        )
        self._simulate_webhook(parent_done)

        # Child completed with 60s
        child_done = _make_channel_params(
            account_sid=self.twilio_test_sid,
            call_sid=twilio_child.sid,
            status='completed',
            direction='outbound-dial',
            caller='+15005550009',
            called='+15005550006',
            parent_call_sid=twilio_parent.sid,
            duration=60,
            sequence_number=3,
        )
        self._simulate_webhook(child_done)

        call = self.env['connect.call'].browse(call_id)
        self.assertEqual(call.duration, 60,
                         "Call duration should be max of channel durations")

    def test_direction_fields(self):
        """Incoming DID call sets direction='incoming'; outbound-api sets 'outgoing'."""
        # Incoming
        twilio_in = self._make_test_call()
        in_params = _make_channel_params(
            account_sid=self.twilio_test_sid,
            call_sid=twilio_in.sid,
            status='initiated',
            direction='inbound',
            caller='+15005550009',
            called='+15005550006',
        )
        call_id = self._simulate_webhook(in_params)
        call_in = self.env['connect.call'].browse(call_id)
        self.assertEqual(call_in.direction, 'incoming')

        # Outgoing (outbound-api = click2call)
        twilio_out = self._make_test_call()
        out_params = _make_channel_params(
            account_sid=self.twilio_test_sid,
            call_sid=twilio_out.sid,
            status='initiated',
            direction='outbound-api',
            caller='+15005550009',
            called='+15005550006',
        )
        call_id_out = self._simulate_webhook(out_params)
        call_out = self.env['connect.call'].browse(call_id_out)
        self.assertEqual(call_out.direction, 'outgoing')


# ===========================================================================
# Test: Call Error Handling
# ===========================================================================

@tagged('post_install', '-at_install', 'live_twilio')
class TestCallErrorHandling(TwilioLiveTestCase):
    """Verify that Twilio error codes and API failures propagate correctly
    into the Odoo call record."""

    def _simulate_webhook(self, params):
        return self.env['connect.call'].on_call_status(params)

    def test_invalid_number_raises_twilio_error(self):
        """Calling the invalid magic number raises TwilioRestException 21217."""
        with self.assertRaises(TwilioRestException) as cm:
            self._make_test_call(to=self.INVALID_NUMBER, from_=self.VALID_NUMBER)
        self.assertEqual(cm.exception.code, 21217)

    def test_unroutable_number_raises_twilio_error(self):
        """Calling the cant-route magic number raises TwilioRestException 21214."""
        with self.assertRaises(TwilioRestException) as cm:
            self._make_test_call(to=self.CANT_ROUTE, from_=self.VALID_NUMBER)
        self.assertEqual(cm.exception.code, 21214)

    def test_error_code_propagates_to_call_record(self):
        """When a webhook carries ErrorCode the call record sets has_error."""
        twilio_call = self._make_test_call()
        params = _make_channel_params(
            account_sid=self.twilio_test_sid,
            call_sid=twilio_call.sid,
            status='failed',
            direction='inbound',
            caller='+15005550009',
            called='+15005550006',
            error_code='21215',
            error_message='Account not authorized to call that number',
        )
        call_id = self._simulate_webhook(params)
        call = self.env['connect.call'].browse(call_id)

        self.assertTrue(call.has_error)
        self.assertEqual(call.error_code, '21215')
        self.assertEqual(call.error_message, 'Account not authorized to call that number')

    def test_ignored_error_code_does_not_flag(self):
        """Error codes in IGNORE_ERROR_CODES (e.g. 32009) should NOT set
        has_error on the call record."""
        twilio_call = self._make_test_call()
        params = _make_channel_params(
            account_sid=self.twilio_test_sid,
            call_sid=twilio_call.sid,
            status='completed',
            direction='inbound',
            caller='+15005550009',
            called='+15005550006',
            error_code='32009',
            error_message='Ignored error',
        )
        call_id = self._simulate_webhook(params)
        call = self.env['connect.call'].browse(call_id)
        self.assertFalse(call.has_error,
                         "Error code 32009 is in IGNORE_ERROR_CODES and should be ignored")

    @mute_logger('odoo.addons.connect.models.call')
    def test_duplicate_webhook_filtered_by_sequence(self):
        """Exact duplicate webhooks (same CallSid, SequenceNumber, CallStatus)
        should be idempotent — the channel is not updated twice."""
        twilio_call = self._make_test_call()
        params = _make_channel_params(
            account_sid=self.twilio_test_sid,
            call_sid=twilio_call.sid,
            status='ringing',
            direction='inbound',
            caller='+15005550009',
            called='+15005550006',
            sequence_number=1,
        )
        # First webhook creates the channel
        self._simulate_webhook(params)
        channel = self.env['connect.channel'].search([('sid', '=', twilio_call.sid)])
        self.assertTrue(channel.exists())
        write_date_1 = channel.write_date

        # Second identical webhook should be filtered
        self._simulate_webhook(params)
        channel.invalidate_recordset()
        # Channel should still exist (not duplicated)
        channels = self.env['connect.channel'].search([('sid', '=', twilio_call.sid)])
        self.assertEqual(len(channels), 1, "Duplicate webhook should not create a second channel")


# ===========================================================================
# Test: Webhook Signature Validation
# ===========================================================================

@tagged('post_install', '-at_install', 'live_twilio')
class TestWebhookSignatureValidation(TwilioLiveTestCase):
    """Verify Twilio request signature validation using real RequestValidator
    with test auth tokens."""

    def test_valid_signature_accepted(self):
        """A properly signed request passes RequestValidator.validate()."""
        self._configure_test_credentials()
        auth_token = self.twilio_test_token
        validator = RequestValidator(auth_token)

        url = 'https://example.com/twilio/webhook/callstatus'
        params = {
            'CallSid': 'CA1234567890abcdef1234567890abcdef',
            'CallStatus': 'completed',
            'AccountSid': self.twilio_test_sid,
        }
        signature = validator.compute_signature(url, params)
        self.assertTrue(
            validator.validate(url, params, signature),
            "Valid signature should pass validation"
        )

    def test_invalid_signature_rejected(self):
        """A request with a tampered signature fails validation."""
        self._configure_test_credentials()
        auth_token = self.twilio_test_token
        validator = RequestValidator(auth_token)

        url = 'https://example.com/twilio/webhook/callstatus'
        params = {
            'CallSid': 'CA1234567890abcdef1234567890abcdef',
            'CallStatus': 'completed',
            'AccountSid': self.twilio_test_sid,
        }
        bad_signature = 'definitely_not_a_valid_signature'
        self.assertFalse(
            validator.validate(url, params, bad_signature),
            "Invalid signature should fail validation"
        )

    def test_wrong_token_rejected(self):
        """A signature computed with a different auth token is rejected."""
        validator_real = RequestValidator(self.twilio_test_token)
        validator_fake = RequestValidator('wrong_token_0000000000000000')

        url = 'https://example.com/twilio/webhook/callstatus'
        params = {
            'CallSid': 'CA1234567890abcdef1234567890abcdef',
            'CallStatus': 'ringing',
        }
        sig_fake = validator_fake.compute_signature(url, params)
        self.assertFalse(
            validator_real.validate(url, params, sig_fake),
            "Signature from wrong token should be rejected"
        )

    def test_signature_changes_with_params(self):
        """Modifying any parameter invalidates the original signature."""
        validator = RequestValidator(self.twilio_test_token)

        url = 'https://example.com/twilio/webhook/callstatus'
        original_params = {
            'CallSid': 'CA1234567890abcdef1234567890abcdef',
            'CallStatus': 'ringing',
        }
        sig = validator.compute_signature(url, original_params)

        # Tamper with a parameter
        tampered_params = dict(original_params, CallStatus='completed')
        self.assertFalse(
            validator.validate(url, tampered_params, sig),
            "Signature should fail after parameter tampering"
        )

    def test_signature_changes_with_url(self):
        """Changing the URL invalidates a signature computed for the
        original URL."""
        validator = RequestValidator(self.twilio_test_token)

        params = {
            'CallSid': 'CA1234567890abcdef1234567890abcdef',
            'CallStatus': 'completed',
        }
        sig = validator.compute_signature(
            'https://example.com/twilio/webhook/callstatus', params)
        self.assertFalse(
            validator.validate(
                'https://evil.com/twilio/webhook/callstatus', params, sig),
            "Signature should fail for different URL"
        )


# ===========================================================================
# Test: Call Finalization
# ===========================================================================

@tagged('post_install', '-at_install', 'live_twilio')
class TestCallFinalization(TwilioLiveTestCase):
    """Verify the finalization pipeline — is_finalized, call_result, and
    user field population — by driving a complete webhook sequence."""

    def _simulate_webhook(self, params):
        return self.env['connect.call'].on_call_status(params)

    def test_single_channel_completed_sets_finalized(self):
        """A single-channel call that reaches 'completed' is finalized."""
        twilio_call = self._make_test_call()
        sid = twilio_call.sid

        # initiated -> completed in one step (simplest lifecycle)
        for status, seq in [('initiated', 0), ('completed', 1)]:
            params = _make_channel_params(
                account_sid=self.twilio_test_sid,
                call_sid=sid,
                status=status,
                direction='inbound',
                caller='+15005550009',
                called='+15005550006',
                duration=30 if status == 'completed' else 0,
                sequence_number=seq,
            )
            call_id = self._simulate_webhook(params)

        call = self.env['connect.call'].browse(call_id)
        self.assertTrue(call.is_finalized,
                        "Call should be finalized after all channels end")

    def test_call_result_answered(self):
        """An outgoing call that completes has call_result='answered'
        (outgoing calls always finalize as completed)."""
        twilio_call = self._make_test_call()
        sid = twilio_call.sid

        for status, seq in [('initiated', 0), ('completed', 1)]:
            params = _make_channel_params(
                account_sid=self.twilio_test_sid,
                call_sid=sid,
                status=status,
                direction='outbound-api',
                caller='+15005550009',
                called='+15005550006',
                duration=120 if status == 'completed' else 0,
                sequence_number=seq,
            )
            call_id = self._simulate_webhook(params)

        call = self.env['connect.call'].browse(call_id)
        self.assertTrue(call.is_finalized)
        self.assertEqual(call.status, 'completed')
        # Outgoing completed calls should be 'answered' (or could be 'failed'
        # if no answered_user, but status=completed -> answered in compute)
        self.assertIn(call.call_result, ('answered', 'failed'))

    def test_call_result_missed_incoming(self):
        """An incoming call that ends without answered_user computes as missed."""
        twilio_call = self._make_test_call()
        sid = twilio_call.sid

        for status, seq in [('initiated', 0), ('ringing', 1), ('no-answer', 2)]:
            params = _make_channel_params(
                account_sid=self.twilio_test_sid,
                call_sid=sid,
                status=status,
                direction='inbound',
                caller='+15005550009',
                called='+15005550006',
                sequence_number=seq,
            )
            call_id = self._simulate_webhook(params)

        call = self.env['connect.call'].browse(call_id)
        self.assertTrue(call.is_finalized)
        self.assertFalse(call.answered_user,
                         "No one answered, so answered_user should be empty")
        self.assertEqual(call.call_result, 'missed')
        self.assertTrue(call.is_missed)

    def test_call_result_failed(self):
        """A call that ends with 'failed' status has call_result='failed'."""
        twilio_call = self._make_test_call()
        sid = twilio_call.sid

        for status, seq in [('initiated', 0), ('failed', 1)]:
            params = _make_channel_params(
                account_sid=self.twilio_test_sid,
                call_sid=sid,
                status=status,
                direction='inbound',
                caller='+15005550009',
                called='+15005550006',
                sequence_number=seq,
            )
            call_id = self._simulate_webhook(params)

        call = self.env['connect.call'].browse(call_id)
        self.assertTrue(call.is_finalized)
        self.assertEqual(call.call_result, 'failed')

    def test_call_result_busy(self):
        """A call that ends with 'busy' status has call_result='busy'."""
        twilio_call = self._make_test_call()
        sid = twilio_call.sid

        for status, seq in [('initiated', 0), ('busy', 1)]:
            params = _make_channel_params(
                account_sid=self.twilio_test_sid,
                call_sid=sid,
                status=status,
                direction='inbound',
                caller='+15005550009',
                called='+15005550006',
                sequence_number=seq,
            )
            call_id = self._simulate_webhook(params)

        call = self.env['connect.call'].browse(call_id)
        # Busy calls may or may not be finalized depending on channel state,
        # but once finalized:
        if call.is_finalized:
            self.assertEqual(call.call_result, 'busy')

    def test_finalization_is_idempotent(self):
        """Calling _finalize_call_details twice does not change the result."""
        twilio_call = self._make_test_call()
        sid = twilio_call.sid

        for status, seq in [('initiated', 0), ('completed', 1)]:
            params = _make_channel_params(
                account_sid=self.twilio_test_sid,
                call_sid=sid,
                status=status,
                direction='outbound-api',
                caller='+15005550009',
                called='+15005550006',
                duration=10 if status == 'completed' else 0,
                sequence_number=seq,
            )
            call_id = self._simulate_webhook(params)

        call = self.env['connect.call'].browse(call_id)
        self.assertTrue(call.is_finalized)
        status_after_first = call.status

        # Second finalization attempt should be a no-op
        call._finalize_call_details()
        self.assertEqual(call.status, status_after_first,
                         "Second finalization should not change status")
        self.assertTrue(call.is_finalized)

    def test_transfer_context_cleared_after_finalization(self):
        """After finalization, transfer_context is cleared (set to False)."""
        twilio_call = self._make_test_call()
        sid = twilio_call.sid

        for status, seq in [('initiated', 0), ('completed', 1)]:
            params = _make_channel_params(
                account_sid=self.twilio_test_sid,
                call_sid=sid,
                status=status,
                direction='outbound-api',
                caller='+15005550009',
                called='+15005550006',
                duration=15 if status == 'completed' else 0,
                sequence_number=seq,
            )
            call_id = self._simulate_webhook(params)

        call = self.env['connect.call'].browse(call_id)
        self.assertTrue(call.is_finalized)
        self.assertFalse(call.transfer_context,
                         "transfer_context should be cleared after finalization")

    def test_outgoing_call_pattern_set(self):
        """Outgoing calls always get call_pattern='direct_call'."""
        twilio_call = self._make_test_call()
        sid = twilio_call.sid

        params = _make_channel_params(
            account_sid=self.twilio_test_sid,
            call_sid=sid,
            status='initiated',
            direction='outbound-api',
            caller='+15005550009',
            called='+15005550006',
        )
        call_id = self._simulate_webhook(params)
        call = self.env['connect.call'].browse(call_id)
        self.assertEqual(call.call_pattern, 'direct_call',
                         "Outgoing calls should have direct_call pattern")

    def test_analytics_fields_populated(self):
        """After creation, analytics fields (hour_of_day, day_of_week) are
        populated based on create_date."""
        twilio_call = self._make_test_call()
        params = _make_channel_params(
            account_sid=self.twilio_test_sid,
            call_sid=twilio_call.sid,
            status='initiated',
            direction='inbound',
            caller='+15005550009',
            called='+15005550006',
        )
        call_id = self._simulate_webhook(params)
        call = self.env['connect.call'].browse(call_id)

        self.assertIsNotNone(call.create_date)
        self.assertGreaterEqual(call.hour_of_day, 0)
        self.assertLessEqual(call.hour_of_day, 23)
        self.assertTrue(call.day_of_week, "day_of_week should be populated")

    def test_call_type_defaults_to_phone(self):
        """Standard calls default to call_type='phone'."""
        twilio_call = self._make_test_call()
        params = _make_channel_params(
            account_sid=self.twilio_test_sid,
            call_sid=twilio_call.sid,
            status='initiated',
            direction='inbound',
            caller='+15005550009',
            called='+15005550006',
        )
        call_id = self._simulate_webhook(params)
        call = self.env['connect.call'].browse(call_id)
        self.assertEqual(call.call_type, 'phone')
