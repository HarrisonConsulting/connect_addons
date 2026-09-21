# -*- coding: utf-8 -*-
"""Twilio signature validation on webhook URLs that carry a query string.

Twilio signs the request URL — query string included — plus the POST body
parameters. Odoo merges the query string into the route kwargs, so
validating with those counts every query parameter twice: the
``?done_callflows=`` marker on the ``<Dial>`` action URL made every rejected
call answer "Invalid Twilio request!".
"""
from twilio.request_validator import RequestValidator
from types import SimpleNamespace
from unittest.mock import patch
from werkzeug.exceptions import Unauthorized

from odoo.tests import HttpCase, TransactionCase, tagged, new_test_user

from ..models import ir_http
from ..controllers import twilio_webhooks

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


@tagged('post_install', '-at_install')
class TestTwilioAuthHook(TransactionCase):

    def test_disabled_signature_check_rejects_public_webhook(self):
        """A disabled verifier cannot turn a public callback into an allow-all route."""
        incoming = SimpleNamespace(
            env=self.env, httprequest=SimpleNamespace(form={}, method='POST'),
        )
        with patch.object(twilio_webhooks, 'request', incoming), \
             patch.object(type(self.env['connect.settings']), 'get_param', return_value=False), \
             self.assertLogs('odoo.addons.connect.tools', level='CRITICAL'):
            self.assertFalse(twilio_webhooks.ConnectTwilioController.check_signature())

    def test_provider_credentials_and_log_context_keep_the_secret_private(self):
        """The provider supplies credentials while diagnostics expose only account identity."""
        settings = self.env['connect.settings']
        values = {'account_sid': 'AC123456789', 'auth_token': 'test-secret', 'rest_provider': 'twilio'}
        with patch.object(type(settings), 'get_param', side_effect=lambda name: values.get(name)):
            self.assertEqual(settings._get_client_credentials(), ('AC123456789', 'test-secret'))
            self.assertEqual(settings._provider_log_context(), 'provider=twilio account=AC123456…')

    def test_signature_precedes_public_identity_and_uses_only_post_fields(self):
        """Both hooks verify their credential scope before binding public identity."""
        for method, regional in (
            ('_auth_method_connect_twilio_voice', True),
            ('_auth_method_connect_twilio_account', False),
        ):
            with self.subTest(method=method):
                incoming = SimpleNamespace(
                    env=self.env,
                    httprequest=SimpleNamespace(form={'CallSid': 'CAtest'}),
                    session=SimpleNamespace(can_save=True),
                )
                events = []
                def validate(*args, **kwargs):
                    events.append('validate')
                    return True
                with patch.object(ir_http, 'request', incoming), \
                     patch.object(ir_http, 'validate_twilio_request', side_effect=validate) as validator, \
                     patch.object(ir_http.IrHttp, '_auth_method_public',
                                  side_effect=lambda: events.append('bind')):
                    getattr(ir_http.IrHttp, method)()
                self.assertEqual(events, ['validate', 'bind'])
                self.assertIs(validator.call_args.args[2], incoming.httprequest.form)
                self.assertEqual(validator.call_args.kwargs, {'region': regional})
                self.assertFalse(incoming.session.can_save)
                self.assertTrue(incoming.connect_twilio_authenticated)

    def test_invalid_signature_never_binds_identity(self):
        """A refused callback cannot authenticate or change session persistence."""
        incoming = SimpleNamespace(
            env=self.env, httprequest=SimpleNamespace(form={}),
            session=SimpleNamespace(can_save=True),
        )
        with patch.object(ir_http, 'request', incoming), \
             patch.object(ir_http, 'validate_twilio_request', return_value=False), \
             patch.object(ir_http.IrHttp, '_auth_method_public') as bind:
            with self.assertRaises(Unauthorized):
                ir_http.IrHttp._auth_method_connect_twilio_voice()
        bind.assert_not_called()
        self.assertTrue(incoming.session.can_save)
        self.assertFalse(hasattr(incoming, 'connect_twilio_authenticated'))
