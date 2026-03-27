# -*- coding: utf-8 -*-
"""Tests for connect.channel model and webhook handling."""

from unittest.mock import patch

from odoo.tests import tagged
from .common import ConnectTestCase


@tagged('post_install', '-at_install')
class TestChannelWebhook(ConnectTestCase):
    """Test on_call_status webhook handler for channel creation and updates."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Channel = cls.env['connect.channel']
        cls.Call = cls.env['connect.call']

        # Base webhook params reusable across tests
        cls.base_params = {
            'CallSid': 'CA1234567890abcdef1234567890abcdef',
            'CallStatus': 'initiated',
            'Direction': 'inbound',
            'Caller': '+15551234567',
            'Called': '+15559876543',
            'To': '+15559876543',
            'CallDuration': '0',
            'SequenceNumber': '1',
        }

    def _make_params(self, **overrides):
        """Return a copy of base_params with optional overrides."""
        params = dict(self.base_params)
        params.update(overrides)
        return params

    def _create_existing_channel(self, **overrides):
        """Create a channel record directly for update-path tests."""
        vals = {
            'sid': self.base_params['CallSid'],
            'caller': '+15551234567',
            'called': '+15559876543',
            'to': '+15559876543',
            'technical_direction': 'inbound',
            'status': 'initiated',
            'duration': 0,
            'call_type': 'phone',
            'sequence_number': 1,
        }
        vals.update(overrides)
        return self.Channel.create(vals)

    # ------------------------------------------------------------------
    # Channel update path (existing channel)
    # ------------------------------------------------------------------

    def test_on_call_status_updates_existing_channel(self):
        """Sending a new status for an existing CallSid updates the channel."""
        channel = self._create_existing_channel()
        params = self._make_params(
            CallStatus='ringing',
            SequenceNumber='2',
        )
        with self.mockTwilioClient():
            self.Channel.on_call_status(params)
        channel.invalidate_recordset()
        self.assertEqual(channel.status, 'ringing')
        self.assertEqual(channel.sequence_number, 2)

    def test_on_call_status_updates_duration(self):
        """Duration is updated from the webhook CallDuration param."""
        channel = self._create_existing_channel()
        params = self._make_params(
            CallStatus='completed',
            CallDuration='42',
            SequenceNumber='3',
        )
        with self.mockTwilioClient():
            self.Channel.on_call_status(params)
        channel.invalidate_recordset()
        self.assertEqual(channel.duration, 42)
        self.assertEqual(channel.status, 'completed')

    def test_on_call_status_preserves_caller_when_empty(self):
        """Empty Caller in a webhook does not overwrite the existing value."""
        channel = self._create_existing_channel(caller='+15551234567')
        params = self._make_params(
            Caller='',
            CallStatus='completed',
            SequenceNumber='2',
        )
        with self.mockTwilioClient():
            self.Channel.on_call_status(params)
        channel.invalidate_recordset()
        self.assertEqual(channel.caller, '+15551234567')

    # ------------------------------------------------------------------
    # Duplicate filtering
    # ------------------------------------------------------------------

    def test_duplicate_webhook_exact_filtered(self):
        """Exact duplicate (same sequence + same status) is silently skipped."""
        channel = self._create_existing_channel(
            status='ringing',
            sequence_number=5,
        )
        params = self._make_params(
            CallStatus='ringing',
            SequenceNumber='5',
        )
        result = self.Channel.on_call_status(params)
        # Duplicate returns None (early return)
        self.assertIsNone(result)
        # Channel unchanged
        channel.invalidate_recordset()
        self.assertEqual(channel.status, 'ringing')
        self.assertEqual(channel.sequence_number, 5)

    def test_duplicate_webhook_different_status_processed(self):
        """Same sequence but different status is NOT filtered."""
        channel = self._create_existing_channel(
            status='ringing',
            sequence_number=5,
        )
        params = self._make_params(
            CallStatus='in-progress',
            SequenceNumber='5',
        )
        with self.mockTwilioClient():
            self.Channel.on_call_status(params)
        channel.invalidate_recordset()
        self.assertEqual(channel.status, 'in-progress')

    def test_duplicate_webhook_different_sequence_processed(self):
        """Different sequence with same status is NOT filtered."""
        channel = self._create_existing_channel(
            status='ringing',
            sequence_number=5,
        )
        params = self._make_params(
            CallStatus='ringing',
            SequenceNumber='6',
        )
        with self.mockTwilioClient():
            self.Channel.on_call_status(params)
        channel.invalidate_recordset()
        self.assertEqual(channel.sequence_number, 6)

    # ------------------------------------------------------------------
    # Channel creation path
    # ------------------------------------------------------------------

    def _mock_get_user_by_uri(self):
        """Patch get_user_by_uri to return empty recordset (no PBX user found)."""
        empty = self.env['connect.user']
        return patch.object(
            type(self.env['connect.user']),
            'get_user_by_uri',
            return_value=empty,
        )

    def _mock_get_partner_by_number(self, partner=None):
        """Patch get_partner_by_number to return a specific partner or empty."""
        result = partner or self.env['res.partner']
        return patch.object(
            type(self.env['res.partner']),
            'get_partner_by_number',
            return_value=result,
        )

    def test_on_call_status_creates_new_channel(self):
        """A CallSid not matching any existing channel creates a new one."""
        params = self._make_params(
            CallSid='CAnew_channel_test_00000000000000000',
        )
        with self.mockTwilioClient(), \
             self._mock_get_user_by_uri(), \
             self._mock_get_partner_by_number(self.partner_1):
            channel = self.Channel.on_call_status(params)
        self.assertTrue(channel.id)
        self.assertEqual(channel.sid, 'CAnew_channel_test_00000000000000000')
        self.assertEqual(channel.caller, '+15551234567')
        self.assertEqual(channel.called, '+15559876543')
        self.assertEqual(channel.status, 'initiated')
        self.assertEqual(channel.technical_direction, 'inbound')
        self.assertEqual(channel.call_type, 'phone')

    def test_on_call_status_new_channel_with_parent(self):
        """ParentCallSid links the new channel to an existing parent channel."""
        parent = self._create_existing_channel(
            sid='CAparent_test_000000000000000000000',
        )
        params = self._make_params(
            CallSid='CAchild_test_0000000000000000000000',
            ParentCallSid='CAparent_test_000000000000000000000',
        )
        with self.mockTwilioClient(), \
             self._mock_get_user_by_uri(), \
             self._mock_get_partner_by_number(self.partner_1):
            child = self.Channel.on_call_status(params)
        self.assertEqual(child.parent_channel.id, parent.id)
        self.assertEqual(child.parent_sid, parent.sid)

    def test_on_call_status_whatsapp_type_detected(self):
        """A whatsapp: prefix on Caller sets call_type to 'whatsapp'."""
        params = self._make_params(
            CallSid='CAwhatsapp_test_00000000000000000000',
            Caller='whatsapp:+15551234567',
            Called='+15559876543',
            To='+15559876543',
        )
        with self.mockTwilioClient(), \
             self._mock_get_user_by_uri(), \
             self._mock_get_partner_by_number(self.partner_1):
            channel = self.Channel.on_call_status(params)
        self.assertEqual(channel.call_type, 'whatsapp')
        # Caller stored with whatsapp prefix stripped
        self.assertEqual(channel.caller, '+15551234567')

    # ------------------------------------------------------------------
    # Computed fields
    # ------------------------------------------------------------------

    def test_channel_number_e164(self):
        """Plain E.164 number passes through unchanged."""
        channel = self._create_existing_channel(
            caller='+15551234567',
            called='+15559876543',
        )
        self.assertEqual(channel.caller_number, '+15551234567')
        self.assertEqual(channel.called_number, '+15559876543')

    def test_channel_number_whatsapp_stripped(self):
        """whatsapp: prefix is stripped from number fields."""
        channel = self._create_existing_channel(
            caller='whatsapp:+15551234567',
            called='whatsapp:+15559876543',
        )
        self.assertEqual(channel.caller_number, '+15551234567')
        self.assertEqual(channel.called_number, '+15559876543')

    def test_channel_number_client_format(self):
        """client:12345678 format extracts the numeric identifier."""
        channel = self._create_existing_channel(
            caller='client:12345678',
            called='+15559876543',
        )
        self.assertEqual(channel.caller_number, '12345678')

    def test_duration_human_format(self):
        """90 seconds renders as '01:30'."""
        channel = self._create_existing_channel(duration=90)
        self.assertEqual(channel.duration_human, '01:30')

    def test_duration_human_zero(self):
        """0 seconds renders as '00:00'."""
        channel = self._create_existing_channel(duration=0)
        self.assertEqual(channel.duration_human, '00:00')

    def test_duration_human_none(self):
        """None duration renders as '00:00'."""
        channel = self.Channel.new({'duration': None})
        channel._get_duration_human()
        self.assertEqual(channel.duration_human, '00:00')

    # ------------------------------------------------------------------
    # Call source tagging
    # ------------------------------------------------------------------

    def test_call_source_parent_ring_group(self):
        """Child of a ring_group call gets call_source='ring_group'."""
        call = self.Call.create({
            'direction': 'incoming',
            'status': 'in-progress',
            'caller': '+15551234567',
            'called': '+15559876543',
            'call_pattern': 'ring_group',
        })
        parent = self._create_existing_channel(
            sid='CArg_parent_test_000000000000000000',
            call=call.id,
        )
        params = self._make_params(
            CallSid='CArg_child_test_0000000000000000000',
            ParentCallSid='CArg_parent_test_000000000000000000',
        )
        with self.mockTwilioClient(), \
             self._mock_get_user_by_uri(), \
             self._mock_get_partner_by_number(self.partner_1):
            child = self.Channel.on_call_status(params)
        self.assertEqual(child.call_source, 'ring_group')

    def test_call_source_parent_direct_call_external_dial(self):
        """Child of a direct_call to an external number gets 'external_dial'."""
        call = self.Call.create({
            'direction': 'incoming',
            'status': 'in-progress',
            'caller': '+15551234567',
            'called': '+15559876543',
            'call_pattern': 'direct_call',
        })
        parent = self._create_existing_channel(
            sid='CAdc_parent_test_000000000000000000',
            call=call.id,
        )
        params = self._make_params(
            CallSid='CAdc_child_test_0000000000000000000',
            ParentCallSid='CAdc_parent_test_000000000000000000',
            Called='+15553334444',
        )
        with self.mockTwilioClient(), \
             self._mock_get_user_by_uri(), \
             self._mock_get_partner_by_number(self.partner_1):
            child = self.Channel.on_call_status(params)
        self.assertEqual(child.call_source, 'external_dial')

    # ------------------------------------------------------------------
    # Update path: direction and call_type
    # ------------------------------------------------------------------

    def test_on_call_status_update_changes_direction(self):
        """Direction is updated when a new webhook provides a different value."""
        channel = self._create_existing_channel(
            technical_direction='inbound',
        )
        params = self._make_params(
            Direction='outbound-dial',
            CallStatus='in-progress',
            SequenceNumber='2',
        )
        with self.mockTwilioClient():
            self.Channel.on_call_status(params)
        channel.invalidate_recordset()
        self.assertEqual(channel.technical_direction, 'outbound-dial')

    def test_on_call_status_returns_channel(self):
        """on_call_status returns the channel record on both paths."""
        # Update path
        channel = self._create_existing_channel()
        params = self._make_params(
            CallStatus='ringing',
            SequenceNumber='2',
        )
        with self.mockTwilioClient():
            result = self.Channel.on_call_status(params)
        self.assertEqual(result.id, channel.id)

        # Create path
        params2 = self._make_params(
            CallSid='CAreturn_test_000000000000000000000',
            CallStatus='initiated',
        )
        with self.mockTwilioClient(), \
             self._mock_get_user_by_uri(), \
             self._mock_get_partner_by_number(self.partner_1):
            result2 = self.Channel.on_call_status(params2)
        self.assertTrue(result2.id)
        self.assertEqual(result2.sid, 'CAreturn_test_000000000000000000000')
