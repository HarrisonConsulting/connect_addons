# -*- coding: utf-8 -*-
"""Tests for connect.settings model."""

from odoo.tests import tagged
from .common import ConnectTestCase


@tagged('post_install', '-at_install')
class TestSettings(ConnectTestCase):
    """Test connect.settings model functionality."""

    def test_settings_singleton(self):
        """Test that get_param returns consistent settings."""
        result1 = self.env['connect.settings'].get_param('debug_mode')
        result2 = self.env['connect.settings'].get_param('debug_mode')
        self.assertEqual(result1, result2)

    def test_get_param(self):
        """Test get_param method."""
        result = self.env['connect.settings'].get_param('twilio_region')
        # Should return a value or False, not raise error
        self.assertIn(type(result), [str, bool, type(None)])

    def test_set_param(self):
        """Test set_param method."""
        test_value = 'AC_test_sid_12345'
        self.env['connect.settings'].set_param('account_sid', test_value)
        result = self.env['connect.settings'].get_param('account_sid')
        self.assertEqual(result, test_value)


@tagged('post_install', '-at_install')
class TestSettingsTwilioClient(ConnectTestCase):
    """Test Twilio client creation."""

    def test_get_client_with_mock(self):
        """Test get_client with mocked Twilio."""
        with self.mockTwilioClient() as mock_client:
            client = self.env['connect.settings'].get_client()
            self.assertIsNotNone(client)
            self.assertEqual(client.region, 'us1')
