# -*- coding: utf-8 -*-
from unittest.mock import MagicMock, patch

from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestMessage(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Message = self.env['connect.message']
        self.own = self.env['connect.voiceml.outgoing_callerid'].create(
            {'number': '+18005550001', 'is_default': True})
        self.user = self.env['res.users'].create({
            'name': 'VoiceML Sender', 'login': 'voiceml_sender',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        self.user.connect_user.voiceml_outgoing_callerid = self.own
        self.client = MagicMock()
        self.client.messages.create.return_value = MagicMock(
            sid='SM-send-1', error_code=None)
        patcher = patch.object(
            type(self.env['connect.settings']), '_voiceml_client',
            return_value=self.client)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(
            type(self.env['connect.settings']), '_get_message_provider',
            return_value='voiceml')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_cannot_note_unreadable_record(self):
        rule_model = self.env['ir.model'].search([('model', '=', 'ir.config_parameter')])
        # ir.config_parameter is readable by the system group only.
        param = self.env['ir.config_parameter'].sudo().create(
            {'key': 'voiceml.test.key', 'value': '1'})
        self.assertTrue(rule_model)
        with self.assertRaises(AccessError):
            self.Message.with_user(self.user).send(
                '+15551230000', 'hi', res_id=param.id,
                res_model='ir.config_parameter')
        self.client.messages.create.assert_not_called()

    def test_cannot_send_from_foreign_number(self):
        with self.assertRaises(AccessError):
            self.Message.with_user(self.user).send(
                '+15551230000', 'hi', outgoing_callerid='+19995550000')
        self.client.messages.create.assert_not_called()

    def test_replayed_inbound_sid_creates_one_row(self):
        params = {'SmsStatus': 'received', 'MessageSid': 'SM-replay-1',
                  'From': '+15551230000', 'To': '+18005550001', 'Body': 'yo'}
        self.Message._receive_voiceml(params)
        self.Message._receive_voiceml(params)
        self.assertEqual(
            self.Message.sudo().search_count([('message_sid', '=', 'SM-replay-1')]), 1)
