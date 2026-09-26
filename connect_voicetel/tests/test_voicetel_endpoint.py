# -*- coding: utf-8 -*-
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from odoo.tests import TransactionCase, tagged

from ..models.webhook import sign_request


@tagged('post_install', '-at_install')
class TestVoicetelEndpoint(TransactionCase):
    """Integration of connect.settings._validate_voicetel_request with the
    connect.settings columns a real deployment configures — the same shape
    connect_twilio proves for _validate_twilio_request, kept as its own
    method so installing both never lets one provider validate (or fail to
    validate) the other's webhooks."""

    def setUp(self):
        super().setUp()
        Settings = self.env['connect.settings'].sudo()
        self.settings = Settings.search([], limit=1) or Settings.with_context(
            no_constrains=True).create({})
        self.settings.with_context(skip_protected_fields=True).write({
            'voicetel_api_key': 'voicetel-secret-key',
            'voicetel_verify_requests': True,
        })

    def _request(self, path='/voicetel/webhook/callstatus', data=None, signature=None):
        headers = {'X-Twilio-Signature': signature} if signature else {}
        return Request(EnvironBuilder(
            base_url='https://odoo.example.test', path=path,
            method='POST', data=data or {}, headers=headers).get_environ())

    def test_valid_signature_is_accepted(self):
        data = {'CallSid': 'CA1', 'CallStatus': 'completed'}
        url = 'https://odoo.example.test/voicetel/webhook/callstatus'
        signature = sign_request(url, data, 'voicetel-secret-key')
        request = self._request(data=data, signature=signature)
        self.assertTrue(
            self.settings._validate_voicetel_request(request, data))

    def test_tampered_signature_is_rejected(self):
        data = {'CallSid': 'CA1', 'CallStatus': 'completed'}
        request = self._request(data=data, signature='bogus-signature')
        self.assertFalse(
            self.settings._validate_voicetel_request(request, data))

    def test_missing_api_key_fails_closed(self):
        self.settings.with_context(skip_protected_fields=True).write({
            'voicetel_api_key': False,
        })
        data = {'CallSid': 'CA1'}
        request = self._request(data=data, signature='irrelevant')
        self.assertFalse(
            self.settings._validate_voicetel_request(request, data))

    def test_verification_disabled_fails_closed(self):
        """Unlike connect_twilio's own switch, disabling verification here
        never means 'accept unsigned' — the same fail-closed policy
        connect_twilio documents for itself."""
        self.settings.write({'voicetel_verify_requests': False})
        data = {'CallSid': 'CA1'}
        request = self._request(data=data, signature='irrelevant')
        self.assertFalse(
            self.settings._validate_voicetel_request(request, data))
