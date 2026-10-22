# -*- coding: utf-8 -*-
"""HTTP-level tests for the signed VoiceTel webhooks.

The routes are auth='public', so the X-Twilio-Signature check is the only
gate. These requests go through the real controller, signed the way
VoiceML signs them.
"""
import json
from unittest.mock import patch

from voiceml.webhooks import compute_signature

from odoo.tests import HttpCase, tagged

from odoo.addons.connect_voicetel.controllers.voicetel_webhooks import signed_url

SECRET = 'test-api-key'
INVALID = 'Invalid VoiceTel request!'


@tagged('post_install', '-at_install')
class TestWebhookHttp(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Settings = cls.env['connect.settings']
        Settings.set_param('voicetel_api_key', SECRET)
        Settings.set_param('voicetel_verify_requests', True)
        Settings.set_param('voicetel_auto_sync', False)
        cls.callflow = cls.env['connect.voicetel.callflow'].create({
            'name': 'HTTP test flow',
            'prompt_message': 'Press one for sales',
            'gather_input': True,
        })

    def _post(self, path, params, secret=SECRET, sign=True):
        url = signed_url(self.base_url() + path)
        headers = {}
        if sign:
            headers['X-Twilio-Signature'] = compute_signature(secret, url, params)
        return self.url_open(path, data=params, headers=headers)

    def _gather_path(self):
        return '/voicetel/webhook/callflow/%d/gather' % self.callflow.id

    def test_signed_gather_runs_as_webhook_user(self):
        response = self._post(self._gather_path(), {'CallSid': 'CA1', 'Digits': '9'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(INVALID, response.text)
        self.assertIn('We received wrong input', response.text)

    def test_unsigned_gather_is_refused(self):
        response = self._post(self._gather_path(), {'Digits': '9'}, sign=False)
        self.assertIn(INVALID, response.text)
        self.assertNotIn('We received wrong input', response.text)

    def test_wrongly_signed_gather_is_refused(self):
        response = self._post(self._gather_path(), {'Digits': '9'}, secret='other-key')
        self.assertIn(INVALID, response.text)

    def test_tampered_params_are_refused(self):
        url = signed_url(self.base_url() + self._gather_path())
        signature = compute_signature(SECRET, url, {'Digits': '1'})
        response = self.url_open(
            self._gather_path(), data={'Digits': '9'},
            headers={'X-Twilio-Signature': signature})
        self.assertIn(INVALID, response.text)

    def test_disabled_verification_rejects_signed_request(self):
        self.env['connect.settings'].set_param('voicetel_verify_requests', False)
        response = self._post(self._gather_path(), {'Digits': '9'})
        self.assertIn(INVALID, response.text)

    def test_empty_api_key_rejects_signed_request(self):
        self.env['connect.settings'].set_param('voicetel_api_key', False)
        response = self._post(self._gather_path(), {'Digits': '9'}, secret='')
        self.assertIn(INVALID, response.text)

    def test_signed_number_webhook_succeeds(self):
        params = {'CallSid': 'CA2', 'Called': '+15550000000', 'Caller': '+15551111111'}
        with patch.object(type(self.env['connect.call']), 'on_voicetel_call_status',
                          return_value=False):
            response = self._post('/voicetel/webhook/number', params)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(INVALID, response.text)
        self.assertIn('Number not found', response.text)

    def test_unsigned_number_webhook_is_refused(self):
        params = {'CallSid': 'CA2', 'Called': '+15550000000'}
        with patch.object(type(self.env['connect.call']), 'on_voicetel_call_status',
                          return_value=False) as handler:
            unsigned = self._post('/voicetel/webhook/number', params, sign=False)
            wrong = self._post('/voicetel/webhook/number', params, secret='other-key')
        self.assertIn(INVALID, unsigned.text)
        self.assertIn(INVALID, wrong.text)
        handler.assert_not_called()

    def test_unsigned_message_is_refused(self):
        with patch.object(type(self.env['connect.message']), 'voicetel_receive',
                          return_value='') as handler:
            response = self._post('/voicetel/webhook/message',
                                  {'MessageSid': 'SM1', 'Body': 'x'}, sign=False)
        self.assertIn(INVALID, response.text)
        handler.assert_not_called()

    def test_unsigned_status_callbacks_answer_empty(self):
        for path, model, method in (
            ('/voicetel/webhook/recordingstatus', 'connect.recording',
             'on_voicetel_recording_status'),
            ('/voicetel/webhook/callstatus', 'connect.call', 'on_voicetel_call_status'),
        ):
            with self.subTest(path=path), patch.object(
                    type(self.env[model]), method, return_value=True) as handler:
                response = self._post(path, {'CallSid': 'CA3'}, sign=False)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.text, '')
                handler.assert_not_called()

    def test_unsigned_call_action_is_refused(self):
        with patch.object(type(self.env['connect.call']), 'on_voicetel_call_action',
                          return_value='<Response/>') as handler:
            response = self._post('/voicetel/webhook/callaction', {'CallSid': 'CA4'},
                                  sign=False)
        self.assertIn(INVALID, response.text)
        handler.assert_not_called()

    def test_gather_handler_is_not_public_rpc(self):
        admin = self.env.ref('base.user_admin')
        admin.password = 'Rpc-surface-pw-1'
        self.authenticate(admin.login, 'Rpc-surface-pw-1')
        response = self.url_open(
            '/web/dataset/call_kw/connect.voicetel.callflow/gather_action',
            data=json.dumps({'jsonrpc': '2.0', 'method': 'call', 'params': {
                'model': 'connect.voicetel.callflow', 'method': 'gather_action',
                'args': [self.callflow.id, {'Digits': '9'}], 'kwargs': {}}}),
            headers={'Content-Type': 'application/json'})
        body = response.json()
        self.assertIn('error', body)
        self.assertNotIn('result', body)
