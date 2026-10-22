# -*- coding: utf-8 -*-
"""Messages: replay, stored XSS, media trust, send access checks."""
from types import SimpleNamespace
from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.tests import tagged, new_test_user

from .common import VoiceTelTestCommon

XSS = '<img src=x onerror=alert(1)>'


@tagged('post_install', '-at_install')
class TestVoiceTelMessage(VoiceTelTestCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.webhook_user = cls.env.ref('connect.user_connect_webhook')
        cls.account_sid = 'AC' + '1' * 32
        settings = cls.env['connect.settings'].sudo()
        settings.set_param('voicetel_account_sid', cls.account_sid)
        settings.set_param('voicetel_auto_sync', False)
        cls.partner = cls.env['res.partner'].create({
            'name': 'SMS Sender', 'phone': '+15550007777'})

    def _params(self, sid, body='hello', **extra):
        params = {
            'AccountSid': self.account_sid,
            'SmsStatus': 'received',
            'MessageSid': sid,
            'From': '+15550007777',
            'To': '+15550001111',
            'Body': body,
            'NumMedia': '0',
        }
        params.update(extra)
        return params

    def _receive(self, params):
        return self.env['connect.message'].with_user(
            self.webhook_user).voicetel_receive(params)

    def test_replayed_message_sid_is_stored_once(self):
        self._receive(self._params('SMreplay0001'))
        self._receive(self._params('SMreplay0001'))
        self.assertEqual(self.env['connect.message'].sudo().search_count(
            [('message_sid', '=', 'SMreplay0001')]), 1)

    def test_inbound_body_is_escaped_in_chatter(self):
        self._receive(self._params('SMxss0001', body=XSS))
        message = self.env['connect.message'].sudo().search(
            [('message_sid', '=', 'SMxss0001')], limit=1)
        self.assertTrue(message.res_model and message.res_id)
        record = self.env[message.res_model].sudo().browse(message.res_id)
        bodies = ''.join(str(b) for b in record.message_ids.mapped('body'))
        self.assertNotIn('<img src=x', bodies)
        self.assertIn('&lt;img', bodies)

    def test_untrusted_media_url_is_dropped(self):
        self._receive(self._params(
            'SMmedia0001', NumMedia='1',
            MediaUrl0='https://evil.example.com/a.jpg',
            MediaContentType0='image/jpeg'))
        message = self.env['connect.message'].sudo().search(
            [('message_sid', '=', 'SMmedia0001')], limit=1)
        self.assertFalse(message.media_url)

    def test_trusted_media_url_is_kept(self):
        host = self.env['connect.settings']._voicetel_api_host()
        url = 'https://%s/media/a.jpg' % host
        self._receive(self._params(
            'SMmedia0002', NumMedia='1', MediaUrl0=url, MediaContentType0='image/jpeg'))
        message = self.env['connect.message'].sudo().search(
            [('message_sid', '=', 'SMmedia0002')], limit=1)
        self.assertEqual(message.media_url, url)

    def test_failure_is_raised_for_a_retry(self):
        with patch.object(type(self.env['res.partner']), 'get_partner_by_number',
                          side_effect=RuntimeError('db down')), \
                self.assertRaises(RuntimeError):
            self._receive(self._params('SMfail0001'))

    def test_direction_uses_own_numbers(self):
        self.env['connect.voicetel.number'].create(
            {'phone_number': '+15550001111', 'sid': 'PNown'})
        self.assertIn('+15550001111', self.env['connect.message']._own_numbers())


@tagged('post_install', '-at_install')
class TestVoiceTelMessageSend(VoiceTelTestCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['connect.settings'].sudo().set_param('voicetel_auto_sync', False)
        CallerId = cls.env['connect.voicetel.outgoing_callerid'].with_context(
            skip_validation=True)
        cls.mine = CallerId.create({
            'number': '+15550003001', 'friendly_name': 'mine',
            'callerid_type': 'number', 'sid': 'PNmine'})
        cls.other = CallerId.create({
            'number': '+15550003002', 'friendly_name': 'other',
            'callerid_type': 'number', 'sid': 'PNother'})
        cls.user = new_test_user(
            cls.env, login='vt_sms_sender',
            groups='base.group_user,connect.group_user')
        cls.connect_user = cls._create_connect_user(
            'vt_sms_sender_cu', voicetel_outgoing_callerid=cls.mine.id)
        cls.connect_user.user = cls.user
        cls.private_partner = cls.env['res.partner'].create({'name': 'Private'})

    def _send(self, **kwargs):
        sent = SimpleNamespace(
            sid='SMsent', account_sid='AC', num_media=0,
            error_code=None, error_message=None)
        Message = self.env['connect.message'].with_user(self.user)
        with patch.object(type(self.env['connect.settings']), '_get_message_provider',
                          return_value='voicetel'), \
                patch.object(type(self.env['connect.message']), 'voicetel_client_send',
                             return_value=sent) as client_send:
            Message.send('+15559990000', XSS, **kwargs)
        return client_send

    def test_cannot_send_from_someone_elses_number(self):
        with self.assertRaises(AccessError):
            self._send(outgoing_callerid=self.other.number)

    def test_can_send_from_own_number(self):
        client_send = self._send(outgoing_callerid=self.mine.number)
        client_send.assert_called_once()

    def test_cannot_attach_to_an_unreadable_record(self):
        rule_model = self.env.ref('base.model_res_partner')
        self.env['ir.rule'].create({
            'name': 'hide private partner', 'model_id': rule_model.id,
            'domain_force': "[('id', '!=', %d)]" % self.private_partner.id,
            'groups': [(4, self.env.ref('base.group_user').id)],
        })
        with self.assertRaises(AccessError):
            self._send(res_model='res.partner', res_id=self.private_partner.id)
