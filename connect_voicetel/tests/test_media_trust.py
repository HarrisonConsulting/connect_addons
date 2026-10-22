# -*- coding: utf-8 -*-
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestMediaTrust(TransactionCase):

    def setUp(self):
        super().setUp()
        self.settings = self.env['connect.settings']
        self.settings.sudo().set_param('voicetel_account_sid', 'AC1')
        self.settings.sudo().set_param('voicetel_api_key', 'key')
        self.host = self.settings._voicetel_api_host()

    def test_trust_truth_table(self):
        cases = [
            ('https://%s/rec/1.wav' % self.host, True),
            ('http://%s/rec/1.wav' % self.host, False),
            ('https://evil.example.com/rec/1.wav', False),
            ('https://user:pw@%s/rec/1.wav' % self.host, False),
            ('https://%s@evil.example.com/x' % self.host, False),
            ('', False),
        ]
        for url, expected in cases:
            with self.subTest(url=url):
                self.assertEqual(self.settings._is_trusted_media_url(url), expected)

    def test_extra_media_host_is_trusted_but_gets_no_key(self):
        self.settings.sudo().set_param('voicetel_media_hosts', 'cdn.voicetel.com, ')
        url = 'https://cdn.voicetel.com/rec/1.wav'
        self.assertTrue(self.settings._is_trusted_media_url(url))
        self.assertIsNone(self.settings._get_media_auth(url))

    def test_media_auth_only_for_the_api_host_over_https(self):
        self.assertEqual(
            self.settings._get_media_auth('https://%s/rec/1.wav' % self.host),
            ('AC1', 'key'))
        self.assertIsNone(self.settings._get_media_auth('http://%s/rec/1.wav' % self.host))
        self.assertIsNone(self.settings._get_media_auth('https://evil.example.com/x'))
        self.assertIsNone(self.settings._get_media_auth(
            'https://u:p@%s/rec/1.wav' % self.host))


@tagged('post_install', '-at_install')
class TestVoicemail(TransactionCase):

    def test_callflow_voicemail_record_ends_in_callaction(self):
        flow = self.env['connect.voicetel.callflow'].create({
            'name': 'VM flow',
            'voicemail_enabled': True,
            'voicemail_prompt': 'Leave a message',
        })
        xml = str(flow.render())
        self.assertIn('<Record', xml)
        self.assertIn('voicetel/webhook/callaction', xml)
        self.assertIn('voicetel/webhook/vm_recordingstatus', xml)
        self.assertIn('method="POST"', xml)

    def test_foreign_voicemail_url_is_refused(self):
        result = self.env['connect.call'].sudo().on_voicetel_vm_recording_status({
            'CallSid': 'CA-no-such-call',
            'RecordingUrl': 'http://169.254.169.254/latest/meta-data/',
            'RecordingDuration': '3',
        })
        self.assertFalse(result)
