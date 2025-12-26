# -*- coding: utf-8 -*-
"""Tests for connect.call model."""

from odoo.tests import tagged
from .common import ConnectTestCase


@tagged('post_install', '-at_install')
class TestCall(ConnectTestCase):
    """Test connect.call model functionality."""

    def test_call_create_basic(self):
        """Test basic call creation."""
        call = self._create_test_call(
            direction='incoming',
            status='completed',
            caller='+15551234567',
            called='+15559876543',
        )
        self.assertTrue(call.id)
        self.assertEqual(call.direction, 'incoming')
        self.assertEqual(call.status, 'completed')
        self.assertEqual(call.caller, '+15551234567')
        self.assertEqual(call.called, '+15559876543')

    def test_call_duration_human_format(self):
        """Test duration_human computed field."""
        call = self._create_test_call()
        call.duration = 125  # 2 minutes 5 seconds
        self.assertEqual(call.duration_human, '02:05')

    def test_call_duration_zero(self):
        """Test duration_human when duration is 0."""
        call = self._create_test_call()
        call.duration = 0
        self.assertEqual(call.duration_human, '00:00')

    def test_call_duration_minutes(self):
        """Test duration_minutes computed field."""
        call = self._create_test_call()
        call.duration = 125
        self.assertAlmostEqual(call.duration_minutes, 2.08, places=2)

    def test_call_with_partner(self):
        """Test call creation with partner."""
        call = self._create_test_call(partner=self.partner_1.id)
        self.assertEqual(call.partner.id, self.partner_1.id)

    def test_call_directions(self):
        """Test incoming and outgoing call directions."""
        incoming = self._create_test_call(direction='incoming')
        outgoing = self._create_test_call(direction='outgoing')

        self.assertEqual(incoming.direction, 'incoming')
        self.assertEqual(outgoing.direction, 'outgoing')


@tagged('post_install', '-at_install')
class TestCallChannel(ConnectTestCase):
    """Test call with channel relationship."""

    def test_call_with_channel(self):
        """Test call creation with associated channel."""
        call = self._create_test_call(create_channel=True)
        self.assertTrue(call.channels)
        self.assertEqual(len(call.channels), 1)
        self.assertEqual(call.channels[0].call.id, call.id)
