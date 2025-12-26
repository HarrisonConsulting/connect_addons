# -*- coding: utf-8 -*-
"""Tests for connect.settings model."""

from odoo.tests import tagged
from .common import ConnectTestCase


@tagged('post_install', '-at_install')
class TestSettings(ConnectTestCase):
    """Test connect.settings model functionality."""

    def test_settings_singleton(self):
        """Test that settings is a singleton."""
        settings1 = self.env['connect.settings'].get_settings()
        settings2 = self.env['connect.settings'].get_settings()
        self.assertEqual(settings1.id, settings2.id)

    def test_get_param(self):
        """Test get_param method."""
        settings = self.env['connect.settings'].get_settings()
        # Test getting a parameter that exists
        result = settings.get_param('twilio_region')
        # Should return a value or False, not raise error
        self.assertIn(type(result), [str, bool, type(None)])

    def test_set_param(self):
        """Test set_param method."""
        settings = self.env['connect.settings'].get_settings()
        test_value = 'test_region_value'
        settings.set_param('twilio_region', test_value)
        result = settings.get_param('twilio_region')
        self.assertEqual(result, test_value)


@tagged('post_install', '-at_install')
class TestSettingsTwilioClient(ConnectTestCase):
    """Test Twilio client creation."""

    def test_get_client_with_mock(self):
        """Test get_client with mocked Twilio."""
        with self.mockTwilioClient() as mock_client:
            client = self.connect_settings.get_client()
            self.assertIsNotNone(client)
            self.assertEqual(client.region, 'us1')
