# -*- coding: utf-8 -*-
from unittest.mock import patch

from twilio.http.http_client import TwilioHttpClient
from twilio.request_validator import RequestValidator
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from odoo.tests import TransactionCase, tagged

HOST = 'voiceml.example.test'


@tagged('post_install', '-at_install')
class TestVoicetelEndpoint(TransactionCase):

    def setUp(self):
        super().setUp()
        icp = self.env['ir.config_parameter'].sudo()
        icp.set_param('connect_voicetel.account_sid', 'ACvoicetel')
        icp.set_param('connect_voicetel.api_key', 'voicetel-key')
        icp.set_param('connect_voicetel.rest_host', HOST)
        Settings = self.env['connect.settings'].sudo()
        self.settings = Settings.search([], limit=1) or Settings.with_context(
            no_constrains=True).create({})
        self.settings.set_param('rest_provider', 'voicetel')

    def _sent_urls(self, client, urls):
        sent = []
        with patch.object(TwilioHttpClient, 'request',
                          side_effect=lambda method, url, *a, **k: sent.append(url)):
            for url in urls:
                client.http_client.request('GET', url)
        return sent

    def test_client_sends_every_twilio_domain_to_the_provider_host(self):
        client = self.settings.get_client()
        self.assertEqual((client.username, client.password), ('ACvoicetel', 'voicetel-key'))
        sent = self._sent_urls(client, [
            'https://api.twilio.com/2010-04-01/Accounts/AC1/IncomingPhoneNumbers.json?PageSize=50',
            'https://messaging.twilio.com/v1/Services',
        ])
        self.assertEqual(sent, [
            'https://%s/2010-04-01/Accounts/AC1/IncomingPhoneNumbers.json?PageSize=50' % HOST,
            'https://%s/v1/Services' % HOST,
        ])

    def test_twilio_provider_keeps_twilio_hosts(self):
        self.settings.set_param('rest_provider', 'twilio')
        self.assertFalse(self.settings._get_rest_api_host())
        self.assertEqual(type(self.settings.get_client().http_client), TwilioHttpClient)

    def test_webhook_signature_uses_the_provider_token(self):
        self.settings.set_param('twilio_verify_requests', True)
        url = 'https://odoo.example.test/twilio/webhook/callstatus'
        data = {'CallSid': 'CA1', 'CallStatus': 'completed'}
        signature = RequestValidator('voicetel-key').compute_signature(url, data)
        request = Request(EnvironBuilder(
            base_url='https://odoo.example.test', path='/twilio/webhook/callstatus',
            method='POST', data=data,
            headers={'X-Twilio-Signature': signature}).get_environ())
        self.assertTrue(self.settings._validate_twilio_request(request, data))

    def test_media_on_the_provider_host_gets_provider_credentials(self):
        self.assertEqual(
            self.settings.get_media_auth('https://%s/2010-04-01/Recordings/RE1' % HOST),
            ('ACvoicetel', 'voicetel-key'),
        )
