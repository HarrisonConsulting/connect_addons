# -*- coding: utf-8 -*-
"""HTTP-level tests for the signed VoiceML webhooks.

The routes are auth='public', so the X-Twilio-Signature check is the only
gate. These requests go through the real controller, signed the way
`controllers.webhooks.check_signature` verifies them.
"""
import json
from unittest.mock import patch

from odoo.tests import HttpCase, tagged

from odoo.addons.connect_voiceml.models.webhook import normalize_url, sign_request

SECRET = 'test-auth-token'
INVALID = 'Invalid VoiceML request!'


@tagged('post_install', '-at_install')
class TestWebhookHttp(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Settings = cls.env['connect.settings']
        Settings.set_param('voiceml_api_key', SECRET)
        Settings.set_param('voiceml_verify_requests', True)
        cls.callflow = cls.env['connect.voiceml.callflow'].create({
            'name': 'HTTP test flow',
            'prompt_message': 'Press one for sales',
            'gather_input': True,
        })

    def _post(self, path, params, secret=SECRET, sign=True):
        # The controller validates against the https form of the request URL.
        url = normalize_url(self.base_url() + path)
        headers = {}
        if sign:
            headers['X-Twilio-Signature'] = sign_request(url, params, secret)
        return self.url_open(path, data=params, headers=headers)

    def _gather_path(self):
        return '/voiceml/webhook/callflow/%d/gather' % self.callflow.id

    def test_signed_gather_runs_as_webhook_user(self):
        # Digits matches no choice, so the webhook user must read the callflow
        # and render its invalid-input prompt (the H6 ACL path).
        response = self._post(self._gather_path(), {'CallSid': 'CA1', 'Digits': '9'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(INVALID, response.text)
        self.assertIn('We received wrong input', response.text)

    def test_unsigned_gather_is_refused(self):
        response = self._post(self._gather_path(), {'Digits': '9'}, sign=False)
        self.assertIn(INVALID, response.text)
        self.assertNotIn('We received wrong input', response.text)

    def test_wrongly_signed_gather_is_refused(self):
        response = self._post(self._gather_path(), {'Digits': '9'}, secret='other-token')
        self.assertIn(INVALID, response.text)
        self.assertNotIn('We received wrong input', response.text)

    def test_tampered_params_are_refused(self):
        url = normalize_url(self.base_url() + self._gather_path())
        signature = sign_request(url, {'Digits': '1'}, SECRET)
        response = self.url_open(
            self._gather_path(), data={'Digits': '9'},
            headers={'X-Twilio-Signature': signature})
        self.assertIn(INVALID, response.text)

    def test_signed_number_webhook_succeeds(self):
        params = {'CallSid': 'CA2', 'Called': '+15550000000', 'Caller': '+15551111111'}
        with patch.object(type(self.env['connect.call']), '_on_call_status_voiceml',
                          return_value=False):
            response = self._post('/voiceml/webhook/number', params)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(INVALID, response.text)
        self.assertIn('Number not found', response.text)

    def test_unsigned_number_webhook_is_refused(self):
        params = {'CallSid': 'CA2', 'Called': '+15550000000'}
        with patch.object(type(self.env['connect.call']), '_on_call_status_voiceml',
                          return_value=False) as handler:
            unsigned = self._post('/voiceml/webhook/number', params, sign=False)
            wrong = self._post('/voiceml/webhook/number', params, secret='other-token')
        self.assertIn(INVALID, unsigned.text)
        self.assertIn(INVALID, wrong.text)
        handler.assert_not_called()

    def test_disabled_verification_rejects_signed_request(self):
        self.env['connect.settings'].set_param('voiceml_verify_requests', False)
        response = self._post(self._gather_path(), {'Digits': '9'})
        self.assertIn(INVALID, response.text)

    def test_gather_handler_is_not_public_rpc(self):
        admin = self.env.ref('base.user_admin')
        admin.password = 'Rpc-surface-pw-1'
        self.authenticate(admin.login, 'Rpc-surface-pw-1')
        response = self.url_open(
            '/web/dataset/call_kw/connect.voiceml.callflow/_gather_action',
            data=json.dumps({'jsonrpc': '2.0', 'method': 'call', 'params': {
                'model': 'connect.voiceml.callflow', 'method': '_gather_action',
                'args': [self.callflow.id, {'Digits': '9'}], 'kwargs': {}}}),
            headers={'Content-Type': 'application/json'})
        body = response.json()
        self.assertIn('error', body)
        self.assertNotIn('result', body)
