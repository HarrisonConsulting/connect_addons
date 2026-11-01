# -*- coding: utf-8 -*-
"""Tests for connect.message model."""

from odoo.tests import tagged
from .common import ConnectTestCase


@tagged('post_install', '-at_install')
class TestMessage(ConnectTestCase):
    """Test connect.message model CRUD, computed fields, and data handling."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Message = cls.env['connect.message']
        cls.base_vals = {
            'message_sid': 'SM' + 'a' * 32,
            'from_number': '+15551234567',
            'to_number': '+15559876543',
            'body': 'Hello from test',
        }

    # ------------------------------------------------------------------
    # Message CRUD
    # ------------------------------------------------------------------

    def test_message_create_basic(self):
        """Create a message with minimal required fields."""
        msg = self.Message.create(self.base_vals)
        self.assertTrue(msg.id)
        self.assertEqual(msg.message_sid, self.base_vals['message_sid'])
        self.assertEqual(msg.from_number, '+15551234567')
        self.assertEqual(msg.to_number, '+15559876543')
        self.assertEqual(msg.body, 'Hello from test')

    def test_message_create_with_partner(self):
        """Partner is linked correctly when provided."""
        vals = dict(self.base_vals, partner=self.partner_1.id)
        msg = self.Message.create(vals)
        self.assertEqual(msg.partner, self.partner_1)

    # ------------------------------------------------------------------
    # Direction computation
    # ------------------------------------------------------------------

    def test_direction_outgoing_with_sender_user(self):
        """Message with sender_user set is computed as outgoing."""
        vals = dict(self.base_vals, sender_user=self.env.user.id)
        msg = self.Message.create(vals)
        self.assertEqual(msg.direction, 'outgoing')

    def test_direction_incoming_received_status(self):
        """Message with status='received' is computed as incoming."""
        vals = dict(self.base_vals, status='received')
        msg = self.Message.sudo().create(vals)
        self.assertEqual(msg.direction, 'incoming')

    def test_direction_incoming_no_sender(self):
        """Message without sender_user and not 'received' falls back to
        number matching logic -- with no matching connect.number records
        the from_number is not ours, so direction should be incoming."""
        vals = dict(self.base_vals, status='queued')
        msg = self.Message.sudo().create(vals)
        # No connect.number records match from_number, so treated as incoming
        self.assertEqual(msg.direction, 'incoming')

    # ------------------------------------------------------------------
    # Media widget
    # ------------------------------------------------------------------

    def test_media_widget_image(self):
        """Image media type produces an <img> tag."""
        vals = dict(
            self.base_vals,
            media_url='https://example.com/photo.jpg',
            media_content_type='image/jpeg',
        )
        msg = self.Message.create(vals)
        self.assertIn('<img', msg.media_widget)
        self.assertIn('https://example.com/photo.jpg', msg.media_widget)

    def test_media_widget_audio(self):
        """Audio media type produces an <audio> tag."""
        vals = dict(
            self.base_vals,
            media_url='https://example.com/clip.mp3',
            media_content_type='audio/mpeg',
        )
        msg = self.Message.create(vals)
        self.assertIn('<audio', msg.media_widget)
        self.assertIn('controls', msg.media_widget)
        self.assertIn('https://example.com/clip.mp3', msg.media_widget)

    def test_media_widget_video(self):
        """Video media type produces a <video> tag."""
        vals = dict(
            self.base_vals,
            media_url='https://example.com/movie.mp4',
            media_content_type='video/mp4',
        )
        msg = self.Message.create(vals)
        self.assertIn('<video', msg.media_widget)
        self.assertIn('controls', msg.media_widget)
        self.assertIn('https://example.com/movie.mp4', msg.media_widget)

    def test_media_widget_other(self):
        """Non-image/audio/video media produces a download link."""
        vals = dict(
            self.base_vals,
            media_url='https://example.com/doc.pdf',
            media_content_type='application/pdf',
        )
        msg = self.Message.create(vals)
        self.assertIn('<a', msg.media_widget)
        self.assertIn('Download media', msg.media_widget)
        self.assertIn('https://example.com/doc.pdf', msg.media_widget)

    def test_media_widget_no_url(self):
        """No media_url produces empty widget."""
        msg = self.Message.create(self.base_vals)
        self.assertFalse(msg.media_widget)

    # ------------------------------------------------------------------
    # Status and error tracking
    # ------------------------------------------------------------------

    def test_message_default_status(self):
        """Default status for a new message is 'draft'."""
        msg = self.Message.create(self.base_vals)
        self.assertEqual(msg.status, 'draft')

    def test_message_with_error(self):
        """Error fields are stored correctly."""
        vals = dict(
            self.base_vals,
            has_error=True,
            error_code='30007',
            error_message='Message filtering',
        )
        msg = self.Message.sudo().create(vals)
        self.assertTrue(msg.has_error)
        self.assertEqual(msg.error_code, '30007')
        self.assertEqual(msg.error_message, 'Message filtering')

    # ------------------------------------------------------------------
    # Computed name
    # ------------------------------------------------------------------

    def test_compute_name_with_date(self):
        """Name includes message_type, formatted number, and date."""
        msg = self.Message.create(self.base_vals)
        # create() auto-sets message_type to 'sms' when num_media == 0
        self.assertIn('sms', msg.name)
        self.assertIn('from', msg.name)

    def test_message_type_auto_sms(self):
        """Message with num_media=0 gets message_type 'sms'."""
        msg = self.Message.create(self.base_vals)
        self.assertEqual(msg.message_type, 'sms')

    def test_message_type_auto_mms(self):
        """Message with num_media>0 gets message_type 'mms'."""
        vals = dict(self.base_vals, num_media=1)
        msg = self.Message.create(vals)
        self.assertEqual(msg.message_type, 'mms')

    # ------------------------------------------------------------------
    # Receive message values helper
    # ------------------------------------------------------------------

    def test_get_receive_message_values(self):
        """get_receive_message_values extracts expected keys from params."""
        msg = self.Message.create(self.base_vals)
        params = {
            'MessageSid': 'SM123',
            'From': '+15551112222',
            'To': '+15553334444',
            'Body': 'Test body',
            'NumMedia': '2',
            'FromCity': 'Denver',
            'FromState': 'CO',
            'FromZip': '80202',
            'FromCountry': 'US',
            'AccountSid': 'AC123',
            'MessagingServiceSid': 'MG123',
            'SmsStatus': 'received',
            'MediaContentType0': 'image/png',
            'MediaUrl0': 'https://example.com/img.png',
        }
        vals = msg.get_receive_message_values(params)
        self.assertEqual(vals['message_sid'], 'SM123')
        self.assertEqual(vals['from_number'], '+15551112222')
        self.assertEqual(vals['to_number'], '+15553334444')
        self.assertEqual(vals['body'], 'Test body')
        self.assertEqual(vals['num_media'], 2)
        self.assertEqual(vals['from_city'], 'Denver')
        self.assertEqual(vals['from_state'], 'CO')
        self.assertEqual(vals['from_zip'], '80202')
        self.assertEqual(vals['from_country'], 'US')
        self.assertEqual(vals['account_sid'], 'AC123')
        self.assertEqual(vals['messaging_service_sid'], 'MG123')
        self.assertEqual(vals['status'], 'received')
        self.assertEqual(vals['media_content_type'], 'image/png')
        self.assertEqual(vals['media_url'], 'https://example.com/img.png')
