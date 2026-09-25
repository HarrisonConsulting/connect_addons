# -*- coding: utf-8 -*-
"""Twilio signature validation on webhook URLs that carry a query string.

Twilio signs the request URL — query string included — plus the POST body
parameters. Odoo merges the query string into the route kwargs, so
validating with those counts every query parameter twice: the
``?done_callflows=`` marker on the ``<Dial>`` action URL made every rejected
call answer "Invalid Twilio request!".
"""
from types import SimpleNamespace

from twilio.request_validator import RequestValidator

from odoo.tests import HttpCase, TransactionCase, tagged, new_test_user

AUTH_TOKEN = 'sig_test_auth_token'
PAYLOAD = {
    'CallSid': 'CAsignaturetest',
    'CallStatus': 'in-progress',
    'DialCallStatus': 'busy',
}


@tagged('post_install', '-at_install')
class TestWebhookSignature(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        settings = cls.env['connect.settings'].sudo()
        settings.set_param('auth_token', AUTH_TOKEN)
        settings.set_param('twilio_verify_requests', True)
        odoo_user = new_test_user(cls.env, login='sig_test_user')
        cls.pbx_user = cls.env['connect.user'].with_context(
            no_clear_cache=True, no_twilio_create=True).create({
                'user': odoo_user.id,
                'sip_enabled': False,
                'client_enabled': False,
            })

    def _post(self, path, signed_path=None):
        url = self.base_url() + path
        # The controller https-izes the URL before validating, because that
        # is the scheme Twilio signed.
        signature = RequestValidator(AUTH_TOKEN).compute_signature(
            (self.base_url() + (signed_path or path)).replace(
                'http:', 'https:'),
            PAYLOAD,
        )
        return self.url_open(
            url, data=PAYLOAD, headers={'X-Twilio-Signature': signature})

    def test_action_url_with_query_string_validates(self):
        response = self._post(
            '/twilio/webhook/connect.user/call_action/{}'
            '?done_callflows=1'.format(self.pbx_user.id))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Invalid Twilio request', response.text)

    def test_action_url_without_query_string_still_validates(self):
        response = self._post(
            '/twilio/webhook/connect.user/call_action/{}'.format(
                self.pbx_user.id))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Invalid Twilio request', response.text)

    def test_a_forged_signature_is_still_rejected(self):
        """The fix must not turn validation off."""
        response = self._post(
            '/twilio/webhook/connect.user/call_action/{}'
            '?done_callflows=1'.format(self.pbx_user.id),
            signed_path='/twilio/webhook/connect.user/call_action/{}'
                        '?done_callflows=999'.format(self.pbx_user.id),
        )

        self.assertIn('Invalid Twilio request', response.text)

    def test_verification_disabled_is_rejected(self):
        """Fail closed: a disabled setting is never read as permission."""
        settings = self.env['connect.settings'].sudo()
        settings.set_param('twilio_verify_requests', False)
        try:
            response = self._post(
                '/twilio/webhook/connect.user/call_action/{}'.format(
                    self.pbx_user.id))
            self.assertIn('Invalid Twilio request', response.text)
        finally:
            settings.set_param('twilio_verify_requests', True)

    def test_missing_auth_token_is_rejected(self):
        """Fail closed: no token to validate against means no request passes."""
        settings = self.env['connect.settings'].sudo()
        settings.set_param('auth_token', False)
        try:
            response = self._post(
                '/twilio/webhook/connect.user/call_action/{}'.format(
                    self.pbx_user.id))
            self.assertIn('Invalid Twilio request', response.text)
        finally:
            settings.set_param('auth_token', AUTH_TOKEN)


@tagged('post_install', '-at_install')
class TestValidateTwilioRequest(TransactionCase):
    """Unit coverage of connect.settings._validate_twilio_request itself --
    every public Twilio adapter delegates to this one fail-closed policy."""

    test_url = 'https://example.com/twilio/webhook/status'
    test_data = {'CallSid': 'CA_test', 'CallStatus': 'completed'}

    def _httprequest(self, signature='', *, url=None):
        return SimpleNamespace(
            url=url or self.test_url,
            path='/twilio/webhook/status',
            headers={'X-Twilio-Signature': signature},
        )

    def test_disabled_verification_fails_closed(self):
        settings = self.env['connect.settings'].sudo()
        settings.set_param('twilio_verify_requests', False)
        settings.set_param('auth_token', 'unused-token')
        with self.assertLogs(
                'odoo.addons.connect_twilio.models.settings', level='CRITICAL'):
            self.assertFalse(settings._validate_twilio_request(
                self._httprequest(), self.test_data))

    def test_missing_auth_token_fails_closed(self):
        settings = self.env['connect.settings'].sudo()
        settings.set_param('twilio_verify_requests', True)
        settings.set_param('auth_token', False)
        with self.assertLogs(
                'odoo.addons.connect_twilio.models.settings', level='CRITICAL'):
            self.assertFalse(settings._validate_twilio_request(
                self._httprequest(), self.test_data))

    def test_valid_signature_is_accepted(self):
        settings = self.env['connect.settings'].sudo()
        auth_token = 'test_auth_token'
        settings.set_param('twilio_verify_requests', True)
        settings.set_param('auth_token', auth_token)
        signature = RequestValidator(auth_token).compute_signature(
            self.test_url, self.test_data)
        self.assertTrue(settings._validate_twilio_request(
            self._httprequest(signature), self.test_data))

    def test_invalid_signature_is_rejected(self):
        settings = self.env['connect.settings'].sudo()
        settings.set_param('twilio_verify_requests', True)
        settings.set_param('auth_token', 'test_auth_token')
        with self.assertLogs(
                'odoo.addons.connect_twilio.models.settings', level='ERROR'):
            self.assertFalse(settings._validate_twilio_request(
                self._httprequest('invalid'), self.test_data))

    def test_http_proxy_url_is_validated_as_https(self):
        settings = self.env['connect.settings'].sudo()
        auth_token = 'test_auth_token'
        settings.set_param('twilio_verify_requests', True)
        settings.set_param('auth_token', auth_token)
        signature = RequestValidator(auth_token).compute_signature(
            self.test_url, self.test_data)
        self.assertTrue(settings._validate_twilio_request(
            self._httprequest(
                signature, url=self.test_url.replace('https:', 'http:')),
            self.test_data,
        ))
