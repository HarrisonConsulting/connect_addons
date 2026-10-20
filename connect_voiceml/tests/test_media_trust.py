# -*- coding: utf-8 -*-
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestMediaTrust(TransactionCase):

    def setUp(self):
        super().setUp()
        self.settings = self.env['connect.settings']
        self.host = self.settings._voiceml_api_host()

    def test_trust_truth_table(self):
        cases = [
            ('https://%s/rec/1.mp3' % self.host, True),
            ('http://%s/rec/1.mp3' % self.host, False),
            ('https://evil.example.com/rec/1.mp3', False),
            ('https://user:pw@%s/rec/1.mp3' % self.host, False),
            ('https://%s@evil.example.com/x' % self.host, False),
            ('', False),
        ]
        for url, expected in cases:
            with self.subTest(url=url):
                self.assertEqual(self.settings._is_trusted_media_url(url), expected)

    def test_media_auth_requires_https(self):
        self.assertIsNone(self.settings._get_media_auth('http://%s/rec/1.mp3' % self.host))
        self.assertIsNone(self.settings._get_media_auth('https://evil.example.com/x'))
