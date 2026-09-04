# -*- coding: utf-8 -*-
"""Tests for connect.park_slot model (call parking)."""

from datetime import timedelta
from unittest.mock import patch, MagicMock, PropertyMock

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger
from .common import ConnectTestCase


@tagged('post_install', '-at_install')
class TestParkSlotComputed(ConnectTestCase):
    """Test computed fields on park slots."""

    def test_is_occupied_with_call(self):
        """Slot linked to a call computes is_occupied = True."""
        call = self._create_test_call(direction='incoming', status='in-progress')
        slot = self._ensure_park_slot(1, call=call.id)
        self.assertTrue(slot.is_occupied)

    def test_is_occupied_without_call(self):
        """Slot with no call computes is_occupied = False."""
        slot = self._ensure_park_slot(2, call=False)
        self.assertFalse(slot.is_occupied)

    def test_caller_display_with_partner(self):
        """Caller display shows partner name when call has a partner."""
        call = self._create_test_call(
            direction='incoming', status='in-progress',
            partner=self.partner_1.id,
        )
        slot = self._ensure_park_slot(3, call=call.id)
        self.assertEqual(slot.caller_display, 'Test Partner')

    def test_caller_display_without_partner(self):
        """Caller display shows caller number when no partner."""
        call = self._create_test_call(
            direction='incoming', status='in-progress',
            caller='+15551234567',
        )
        slot = self._ensure_park_slot(4, call=call.id)
        self.assertEqual(slot.caller_display, '+15551234567')

    def test_caller_display_empty(self):
        """Caller display is empty string when no call linked."""
        slot = self._ensure_park_slot(5, call=False)
        self.assertEqual(slot.caller_display, '')

    def test_park_duration_when_occupied(self):
        """Park duration computes elapsed seconds for occupied slot."""
        call = self._create_test_call(direction='incoming', status='in-progress')
        slot = self._ensure_park_slot(
            6, call=call.id,
            parked_at=fields.Datetime.now() - timedelta(seconds=120))
        self.assertGreaterEqual(slot.park_duration, 119)

    def test_park_duration_when_empty(self):
        """Park duration is 0 for empty slot."""
        slot = self._ensure_park_slot(7, call=False)
        self.assertEqual(slot.park_duration, 0)


@tagged('post_install', '-at_install')
class TestParkSlotConstraints(ConnectTestCase):
    """Test slot number constraints."""

    def test_slot_number_below_range(self):
        """Slot number below 1 raises ValidationError."""
        with self.assertRaises(ValidationError):
            self.env['connect.park_slot'].create({'name': 0})

    def test_slot_number_above_range(self):
        """Slot number above configured max raises ValidationError."""
        with self.assertRaises(ValidationError):
            self.env['connect.park_slot'].create({'name': 100})


@tagged('post_install', '-at_install')
class TestSyncSlots(ConnectTestCase):
    """Test sync_slots method."""

    def test_sync_creates_missing_slots(self):
        """sync_slots creates slots up to configured count."""
        ParkSlot = self.env['connect.park_slot']
        # Clear any existing slots
        ParkSlot.search([]).unlink()

        with patch.object(
            self.env['connect.settings'].__class__, 'get_param',
            side_effect=lambda param, default=False: 5 if param == 'park_slot_count' else default,
        ):
            ParkSlot.sync_slots()

        slots = ParkSlot.search([])
        self.assertEqual(len(slots), 5)
        self.assertEqual(sorted(slots.mapped('name')), [1, 2, 3, 4, 5])

    def test_sync_removes_excess_empty_slots(self):
        """sync_slots removes empty slots beyond the configured count."""
        ParkSlot = self.env['connect.park_slot']
        ParkSlot.search([]).unlink()

        # Create 9 slots
        for i in range(1, 10):
            ParkSlot.create({'name': i})

        # Now sync to 5
        with patch.object(
            self.env['connect.settings'].__class__, 'get_param',
            side_effect=lambda param, default=False: 5 if param == 'park_slot_count' else default,
        ):
            ParkSlot.sync_slots()

        slots = ParkSlot.search([])
        self.assertEqual(len(slots), 5)

    def test_sync_preserves_occupied_excess_slots(self):
        """sync_slots does not remove occupied slots even if beyond count."""
        ParkSlot = self.env['connect.park_slot']
        ParkSlot.search([]).unlink()

        # Create slots 1-9
        for i in range(1, 10):
            ParkSlot.create({'name': i})

        # Occupy slot 8
        call = self._create_test_call(direction='incoming', status='in-progress')
        slot8 = ParkSlot.search([('name', '=', 8)])
        slot8.write({'call': call.id})

        # Sync to 5 - slot 8 should remain because it's occupied
        with patch.object(
            self.env['connect.settings'].__class__, 'get_param',
            side_effect=lambda param, default=False: 5 if param == 'park_slot_count' else default,
        ):
            ParkSlot.sync_slots()

        remaining = ParkSlot.search([])
        remaining_numbers = set(remaining.mapped('name'))
        # Slots 1-5 + slot 8 (occupied)
        self.assertIn(8, remaining_numbers)
        self.assertTrue(all(n in remaining_numbers for n in range(1, 6)))


@tagged('post_install', '-at_install')
class TestParkCall(ConnectTestCase):
    """Test park_call workflow."""

    def _make_channels(self, call):
        """Create user and other-party channels for a call."""
        user_ch = self.env['connect.channel'].create({
            'call': call.id,
            'sid': 'CAuser' + 'u' * 28,
            'caller': call.called,
            'called': call.caller,
            'status': 'in-progress',
            'technical_direction': 'outbound-api',
        })
        other_ch = self.env['connect.channel'].create({
            'call': call.id,
            'sid': 'CAother' + 'o' * 27,
            'caller': call.caller,
            'called': call.called,
            'status': 'in-progress',
            'technical_direction': 'inbound',
        })
        return user_ch, other_ch

    def test_park_call_success(self):
        """Successfully parking a call creates/updates slot and returns success."""
        ParkSlot = self.env['connect.park_slot']
        call = self._create_test_call(direction='incoming', status='in-progress')
        user_ch, other_ch = self._make_channels(call)

        with self.mockTwilioClient(), \
             patch.object(
                 self.env['connect.call'].__class__, '_find_call_channels',
                 return_value=(call, user_ch, other_ch),
             ), \
             patch.object(
                 self.env['connect.settings'].__class__, 'connect_reload_view',
             ):
            result = ParkSlot.park_call('CAxxx', '1')

        self.assertTrue(result['success'])
        self.assertEqual(result['slot'], 1)
        slot = ParkSlot.search([('name', '=', 1)], limit=1)
        self.assertTrue(slot.exists())
        self.assertEqual(slot.call.id, call.id)
        self.assertEqual(slot.caller_channel_sid, other_ch.sid)
        self.assertEqual(slot.conference_name, 'park-1')
        self.assertEqual(slot.parked_by.id, self.env.user.id)

    def test_park_call_not_found(self):
        """Returns error when _find_call_channels cannot find the call."""
        ParkSlot = self.env['connect.park_slot']

        with patch.object(
            self.env['connect.call'].__class__, '_find_call_channels',
            return_value=(None, None, None),
        ):
            result = ParkSlot.park_call('CA_nonexistent', '1')

        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'Call not found')

    def test_park_call_no_parties(self):
        """Returns error when call is found but channels are not."""
        ParkSlot = self.env['connect.park_slot']
        call = self._create_test_call(direction='incoming', status='in-progress')

        with patch.object(
            self.env['connect.call'].__class__, '_find_call_channels',
            return_value=(call, None, None),
        ):
            result = ParkSlot.park_call('CAxxx', '2')

        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'Cannot identify call parties')

    def test_park_call_slot_occupied(self):
        """Returns error when target slot already has a parked call."""
        ParkSlot = self.env['connect.park_slot']
        existing_call = self._create_test_call(direction='incoming', status='in-progress')
        self._ensure_park_slot(3, call=existing_call.id)

        new_call = self._create_test_call(direction='incoming', status='in-progress')
        user_ch, other_ch = self._make_channels(new_call)

        with patch.object(
            self.env['connect.call'].__class__, '_find_call_channels',
            return_value=(new_call, user_ch, other_ch),
        ):
            result = ParkSlot.park_call('CAxxx', '3')

        self.assertFalse(result['success'])
        self.assertIn('Slot 3 is occupied', result['error'])

    @mute_logger('odoo.addons.connect.models.park_slot')
    def test_park_call_twilio_error(self):
        """Returns error when Twilio API call raises an exception."""
        ParkSlot = self.env['connect.park_slot']
        call = self._create_test_call(direction='incoming', status='in-progress')
        user_ch, other_ch = self._make_channels(call)

        mock_client = MagicMock()
        mock_client.calls.return_value.update.side_effect = Exception('Twilio API error')

        with patch.object(
            self.env['connect.settings'].__class__, 'get_client',
            return_value=mock_client,
        ), patch.object(
            self.env['connect.call'].__class__, '_find_call_channels',
            return_value=(call, user_ch, other_ch),
        ):
            result = ParkSlot.park_call('CAxxx', '4')

        self.assertFalse(result['success'])
        self.assertIn('Twilio API error', result['error'])


@tagged('post_install', '-at_install')
class TestUnparkCall(ConnectTestCase):
    """Test unpark_call workflow."""

    def _park_slot(self, slot_number, call=None):
        """Helper to occupy a park slot."""
        if not call:
            call = self._create_test_call(direction='incoming', status='in-progress')
        return self._ensure_park_slot(
            slot_number,
            call=call.id,
            caller_channel_sid='CAparked' + 'p' * 26,
            conference_name=f'park-{slot_number}',
            parked_by=self.env.user.id,
        ), call

    def test_unpark_call_success(self):
        """Successfully unparking clears the slot and returns success."""
        ParkSlot = self.env['connect.park_slot']
        slot, call = self._park_slot(1)

        mock_connect_user = MagicMock()
        mock_connect_user.__bool__ = lambda s: True
        mock_connect_user.get_client_identity.return_value = 'agent@test.sip.twilio.com'
        mock_callerid = MagicMock()
        mock_callerid.number = '+15550001111'
        mock_connect_user.outgoing_callerid = mock_callerid

        with self.mockTwilioClient(), \
             patch.object(
                 type(self.env.user), 'connect_user',
                 new_callable=PropertyMock,
                 return_value=mock_connect_user,
             ), \
             patch.object(
                 self.env['connect.settings'].__class__, 'get_param',
                 return_value='https://test.example.com',
             ), \
             patch.object(
                 self.env['connect.settings'].__class__, 'connect_reload_view',
             ):
            result = ParkSlot.unpark_call('1')

        self.assertTrue(result['success'])
        # Slot should be cleared
        self.assertFalse(slot.call)
        self.assertFalse(slot.caller_channel_sid)
        self.assertFalse(slot.conference_name)

    def test_unpark_call_empty_slot(self):
        """Returns error when slot has no parked call."""
        ParkSlot = self.env['connect.park_slot']
        result = ParkSlot.unpark_call('9')
        self.assertFalse(result['success'])
        self.assertIn('Slot 9 is empty', result['error'])

    def test_unpark_call_no_connect_user(self):
        """Returns error when current user has no connect_user."""
        ParkSlot = self.env['connect.park_slot']
        self._park_slot(5)

        with patch.object(
            type(self.env.user), 'connect_user',
            new_callable=PropertyMock,
            return_value=self.env['connect.user'],
        ):
            result = ParkSlot.unpark_call('5')

        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'User not configured for telephony')

    def test_unpark_call_no_callerid(self):
        """Returns error when user has no outgoing caller ID and no default."""
        ParkSlot = self.env['connect.park_slot']
        self._park_slot(6)

        mock_connect_user = MagicMock()
        mock_connect_user.__bool__ = lambda s: True
        mock_connect_user.outgoing_callerid = None

        with patch.object(
            type(self.env.user), 'connect_user',
            new_callable=PropertyMock,
            return_value=mock_connect_user,
        ), patch.object(
            self.env['connect.outgoing_callerid'].__class__, 'search',
            return_value=self.env['connect.outgoing_callerid'],
        ):
            result = ParkSlot.unpark_call('6')

        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'No caller ID available')


@tagged('post_install', '-at_install')
class TestCheckParkTimeouts(ConnectTestCase):
    """Test check_park_timeouts cron method."""

    def _park_slot_timed(self, slot_number, seconds_ago):
        """Helper to create a slot parked N seconds ago.

        Reuses an existing slot with the same number if present (from init()),
        otherwise creates a new one.
        """
        call = self._create_test_call(direction='incoming', status='in-progress')
        parked_at = fields.Datetime.now() - timedelta(seconds=seconds_ago)
        ParkSlot = self.env['connect.park_slot']
        existing = ParkSlot.search([('name', '=', slot_number)], limit=1)
        if existing:
            existing.write({
                'call': call.id,
                'caller_channel_sid': 'CAparked' + 'p' * 26,
                'conference_name': f'park-{slot_number}',
                'parked_by': self.env.user.id,
                'parked_at': parked_at,
            })
            return existing, call
        return ParkSlot.create({
            'name': slot_number,
            'call': call.id,
            'caller_channel_sid': 'CAparked' + 'p' * 26,
            'conference_name': f'park-{slot_number}',
            'parked_by': self.env.user.id,
            'parked_at': parked_at,
        }), call

    def test_timeout_disabled(self):
        """No action when park_timeout is 0."""
        ParkSlot = self.env['connect.park_slot']
        self._park_slot_timed(1, 600)

        with patch.object(
            self.env['connect.settings'].__class__, 'get_param',
            side_effect=lambda param, default=False: 0 if param == 'park_timeout' else default,
        ):
            ParkSlot.check_park_timeouts()

        # Slot should still be occupied
        slot = ParkSlot.search([('name', '=', 1)], limit=1)
        self.assertTrue(slot.call)

    def test_timeout_clears_slot(self):
        """Timed-out slot gets cleared."""
        ParkSlot = self.env['connect.park_slot']
        slot, call = self._park_slot_timed(1, 400)

        mock_connect_user = MagicMock()
        mock_connect_user.__bool__ = lambda s: True
        mock_connect_user.get_client_identity.return_value = 'agent@test.sip.twilio.com'
        mock_callerid = MagicMock()
        mock_callerid.number = '+15550001111'
        mock_connect_user.outgoing_callerid = mock_callerid

        def get_param_side_effect(param, default=False):
            if param == 'park_timeout':
                return 300
            if param == 'api_url':
                return 'https://test.example.com'
            if param == 'twilio_edge':
                return 'ashburn'
            return default

        with self.mockTwilioClient(), \
             patch.object(
                 self.env['connect.settings'].__class__, 'get_param',
                 side_effect=get_param_side_effect,
             ), \
             patch.object(
                 type(self.env.user), 'connect_user',
                 new_callable=PropertyMock,
                 return_value=mock_connect_user,
             ), \
             patch.object(
                 self.env['connect.settings'].__class__, 'connect_reload_view',
             ):
            ParkSlot.check_park_timeouts()

        # Slot should be cleared
        self.assertFalse(slot.call)
        self.assertFalse(slot.caller_channel_sid)

    def test_no_timeout_for_recent_park(self):
        """Recently parked call is not timed out."""
        ParkSlot = self.env['connect.park_slot']
        slot, call = self._park_slot_timed(1, 60)  # Only 60 seconds ago

        with patch.object(
            self.env['connect.settings'].__class__, 'get_param',
            side_effect=lambda param, default=False: 300 if param == 'park_timeout' else default,
        ):
            ParkSlot.check_park_timeouts()

        # Slot should still be occupied
        self.assertTrue(slot.call)


@tagged('post_install', '-at_install')
class TestGetParkedCalls(ConnectTestCase):
    """Test get_parked_calls API."""

    def test_get_parked_calls_empty(self):
        """Returns empty list when no slots are occupied."""
        ParkSlot = self.env['connect.park_slot']
        result = ParkSlot.get_parked_calls()
        self.assertEqual(result, [])

    def test_get_parked_calls_with_data(self):
        """Returns list of dicts with correct structure for occupied slots."""
        ParkSlot = self.env['connect.park_slot']
        call = self._create_test_call(
            direction='incoming', status='in-progress',
            partner=self.partner_1.id,
            caller='+15551234567',
        )
        slot = self._ensure_park_slot(7, call=call.id, parked_by=self.env.user.id)

        result = ParkSlot.get_parked_calls()
        self.assertEqual(len(result), 1)

        entry = result[0]
        self.assertEqual(entry['slot'], 7)
        self.assertEqual(entry['caller'], 'Test Partner')
        self.assertEqual(entry['parked_by'], self.env.user.name)
        self.assertEqual(entry['call_id'], call.id)
        self.assertTrue(entry['parked_at'])  # ISO timestamp string
        self.assertIn('park_duration', entry)
