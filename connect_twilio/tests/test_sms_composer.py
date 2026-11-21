# -*- coding: utf-8 -*-
"""Twilio contributes From lines only while it is the provider that sends."""
from types import SimpleNamespace
from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import TwilioTestCommon


@tagged('post_install', '-at_install')
class TestTwilioSmsFrom(TwilioTestCommon):

    def test_lines_are_listed_only_while_twilio_sends(self):
        Number = self.env['connect.twilio.number']
        Number.create({'phone_number': '+15551110001'})
        Number.create({'phone_number': '+15551110002', 'is_ignored': True})
        Settings = type(self.env['connect.settings'])
        composer = self.env['sms.composer']
        with patch.object(Settings, '_get_message_provider', return_value='twilio'):
            listed = dict(composer._list_all_numbers())
        self.assertEqual(listed.get('+15551110001'), '+15551110001')
        self.assertNotIn('+15551110002', listed)
        with patch.object(Settings, '_get_message_provider', return_value='voicetel'):
            listed = dict(composer._list_all_numbers())
        self.assertNotIn('+15551110001', listed)
        self.assertNotIn('+15551110002', listed)

    def test_default_from_is_the_users_twilio_line(self):
        caller = self.env['connect.twilio.outgoing_callerid'].create({
            'friendly_name': 'Main',
            'number': '+15551110003',
            'callerid_type': 'number',
        })
        connect_user = self._create_connect_user('smsfromtwilio')
        connect_user.twilio_outgoing_callerid = caller
        Settings = type(self.env['connect.settings'])
        composer = self.env['sms.composer'].with_user(connect_user.user)
        with patch.object(Settings, '_get_message_provider', return_value='twilio'):
            self.assertEqual(composer._default_outgoing_callerid(), '+15551110003')
        with patch.object(Settings, '_get_message_provider', return_value='voicetel'):
            self.assertNotEqual(composer._default_outgoing_callerid(), '+15551110003')

    def test_a_refused_sms_names_the_provider_reason(self):
        def create(**kwargs):
            error = Exception('transport')
            error.msg = (
                "Unable to create record: 'From' +15743383149 is not a "
                "Twilio phone number or Short Code country mismatch")
            raise error

        client = SimpleNamespace(messages=SimpleNamespace(create=create))
        Settings = type(self.env['connect.settings'])
        with patch.object(Settings, 'get_client', return_value=client):
            with self.assertRaises(ValidationError) as caught:
                self.env['connect.message'].client_send(
                    '+12704733663', '+15743383149', 'Did you get this message?')
        self.assertIn('+15743383149 is not a Twilio phone number', str(caught.exception))
        self.assertNotIn('Contact admin', str(caught.exception))

    def test_a_provider_error_code_names_the_reason(self):
        def create(**kwargs):
            return SimpleNamespace(
                error_code=21606,
                error_message='The From phone number is not a Twilio number',
            )

        client = SimpleNamespace(messages=SimpleNamespace(create=create))
        Settings = type(self.env['connect.settings'])
        with patch.object(Settings, 'get_client', return_value=client):
            with self.assertRaisesRegex(ValidationError, 'not a Twilio number'):
                self.env['connect.message'].client_send('+12704733663', '+15745646338', 'hi')

    def test_whatsapp_refusal_leaves_sms_to_be_tried(self):
        def create(**kwargs):
            raise Exception('no whatsapp sender')

        client = SimpleNamespace(messages=SimpleNamespace(create=create))
        Settings = type(self.env['connect.settings'])
        with patch.object(Settings, 'get_client', return_value=client):
            sent = self.env['connect.message'].client_send(
                '+12704733663', '+15745646338', 'hi', whatsapp=True)
        self.assertFalse(sent)
