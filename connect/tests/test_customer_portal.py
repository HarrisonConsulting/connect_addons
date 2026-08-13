# -*- coding: utf-8 -*-
"""Security and rendering tests for customer call-history portal access."""

import base64

from odoo import Command
from odoo.tests import HttpCase, tagged


_CALL_AUDIO = b'ID3customer-call-audio'
_VOICEMAIL_AUDIO = b'RIFFcustomer-voicemail-audio'


@tagged('post_install', '-at_install')
class TestCustomerCallPortal(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        portal_group = cls.env.ref('base.group_portal')
        cls.company = cls.env['res.partner'].create({
            'name': 'Portal Customer Company',
            'is_company': True,
        })
        cls.customer = cls.env['res.partner'].create({
            'name': 'Portal Customer A',
            'email': 'customer-a@example.com',
            'parent_id': cls.company.id,
        })
        cls.sibling_customer = cls.env['res.partner'].create({
            'name': 'Portal Customer B',
            'email': 'customer-b@example.com',
            'parent_id': cls.company.id,
        })
        cls.customer_user = cls.env['res.users'].with_context(
            no_reset_password=True,
        ).create({
            'name': cls.customer.name,
            'login': 'customer-a@example.com',
            'password': 'customer-a-password',
            'partner_id': cls.customer.id,
            'group_ids': [Command.set([portal_group.id])],
        })

        cls.settings = cls.env['connect.settings'].sudo().search([], limit=1)
        if not cls.settings:
            cls.settings = cls.env['connect.settings'].sudo().with_context(
                no_constrains=True,
            ).create({})
        cls.settings.customer_portal_calls_enabled = True

        cls.call_attachment = cls.env['ir.attachment'].sudo().create({
            'name': 'customer_call.mp3',
            'datas': base64.b64encode(_CALL_AUDIO),
            'mimetype': 'audio/mpeg',
        })
        cls.customer_call = cls.env['connect.call'].create({
            'partner': cls.customer.id,
            'direction': 'incoming',
            'status': 'completed',
            'caller': '+15550000001',
            'called': '+15559999999',
            'duration': 75,
        })
        cls.recording = cls.env['connect.recording'].with_context(
            skip_transcription=True,
        ).create({
            'call': cls.customer_call.id,
            'sid': 'RE' + '1' * 32,
            'call_sid': 'CA' + '1' * 32,
            'attachment_id': cls.call_attachment.id,
            'transcript': 'Customer call transcript sentinel.',
            'duration': 75,
            'status': 'completed',
        })

        cls.voicemail_attachment = cls.env['ir.attachment'].sudo().create({
            'name': 'customer_voicemail.wav',
            'datas': base64.b64encode(_VOICEMAIL_AUDIO),
            'mimetype': 'audio/wav',
        })
        cls.customer_voicemail = cls.env['connect.call'].create({
            'partner': cls.customer.id,
            'direction': 'incoming',
            'status': 'no-answer',
            'caller': '+15550000002',
            'called': '+15559999999',
            'voicemail_url': 'https://api.twilio.com/Recordings/REvm.mp3',
            'voicemail_attachment_id': cls.voicemail_attachment.id,
            'voicemail_duration': 18,
            'voicemail_transcript': 'Customer voicemail transcript sentinel.',
        })
        cls.sibling_call = cls.env['connect.call'].create({
            'partner': cls.sibling_customer.id,
            'direction': 'incoming',
            'status': 'completed',
            'caller': '+15550000999',
            'called': '+15559999999',
            'duration': 30,
        })
        cls.env.flush_all()

    def setUp(self):
        super().setUp()
        self.authenticate('customer-a@example.com', 'customer-a-password')

    def test_portal_home_shows_customer_call_card_when_enabled(self):
        response = self.url_open('/my')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Listen to your recordings', response.content)

    def test_list_contains_exact_contact_calls_only(self):
        response = self.url_open('/my/calls')
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            f'/my/calls/{self.customer_call.id}'.encode(), response.content)
        self.assertIn(
            f'/my/calls/{self.customer_voicemail.id}'.encode(), response.content)
        self.assertNotIn(
            f'/my/calls/{self.sibling_call.id}'.encode(), response.content,
            'A sibling contact under the same company must stay private.',
        )
        self.assertNotIn(b'+15550000999', response.content)

    def test_detail_shows_call_recording_transcript(self):
        response = self.url_open(f'/my/calls/{self.customer_call.id}')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Customer call transcript sentinel.', response.content)
        self.assertIn(
            f'/my/calls/{self.customer_call.id}/recording'.encode(),
            response.content,
        )

    def test_detail_shows_voicemail_transcript(self):
        response = self.url_open(f'/my/calls/{self.customer_voicemail.id}')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Customer voicemail transcript sentinel.', response.content)
        self.assertIn(
            f'/my/calls/{self.customer_voicemail.id}/voicemail'.encode(),
            response.content,
        )

    def test_sibling_detail_and_media_are_not_found(self):
        detail = self.url_open(f'/my/calls/{self.sibling_call.id}')
        recording = self.url_open(
            f'/my/calls/{self.sibling_call.id}/recording')
        voicemail = self.url_open(
            f'/my/calls/{self.sibling_call.id}/voicemail')
        self.assertEqual(detail.status_code, 404)
        self.assertEqual(recording.status_code, 404)
        self.assertEqual(voicemail.status_code, 404)

    def test_recording_audio_is_proxied(self):
        response = self.url_open(
            f'/my/calls/{self.customer_call.id}/recording')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, _CALL_AUDIO)
        self.assertEqual(response.headers.get('Content-Type'), 'audio/mpeg')
        self.assertEqual(
            response.headers.get('Cache-Control'), 'private, no-store')

    def test_voicemail_audio_is_proxied(self):
        response = self.url_open(
            f'/my/calls/{self.customer_voicemail.id}/voicemail')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, _VOICEMAIL_AUDIO)
        self.assertEqual(response.headers.get('Content-Type'), 'audio/wav')

    def test_global_setting_disables_home_pages_and_media(self):
        self.settings.customer_portal_calls_enabled = False
        self.env.flush_all()

        home = self.url_open('/my')
        listing = self.url_open('/my/calls')
        detail = self.url_open(f'/my/calls/{self.customer_call.id}')
        recording = self.url_open(
            f'/my/calls/{self.customer_call.id}/recording')

        self.assertNotIn(b'Listen to your recordings', home.content)
        self.assertEqual(listing.status_code, 404)
        self.assertEqual(detail.status_code, 404)
        self.assertEqual(recording.status_code, 404)

    def test_portal_user_has_no_direct_connect_model_access(self):
        self.assertFalse(
            self.env['connect.call'].with_user(self.customer_user).has_access('read'))
        self.assertFalse(
            self.env['connect.recording'].with_user(
                self.customer_user
            ).has_access('read'))
