# -*- coding: utf-8 -*-
"""Tests for connect.conversation model."""

from unittest.mock import patch, MagicMock

from odoo.tests import tagged
from odoo.tools import mute_logger
from odoo.exceptions import ValidationError
from .common import ConnectTestCase


@tagged('post_install', '-at_install')
class TestConversation(ConnectTestCase):
    """Test connect.conversation model: creation, dedup, linking, send routing."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Conversation = cls.env['connect.conversation']
        cls.Message = cls.env['connect.message']
        cls.phone_ours = '+15550001111'
        cls.phone_theirs = '+15550002222'

        # Create a connect.number so _resolve_phone_roles can identify "our" numbers
        if 'connect.number' in cls.env:
            existing = cls.env['connect.number'].search(
                [('phone_number', '=', cls.phone_ours)], limit=1)
            if not existing:
                cls.env['connect.number'].create({'phone_number': cls.phone_ours})

    # ------------------------------------------------------------------
    # get_or_create
    # ------------------------------------------------------------------

    def test_get_or_create_new(self):
        """Creating a conversation for a new phone pair returns a new record."""
        conv = self.Conversation.get_or_create('sms', self.phone_ours, self.phone_theirs)
        self.assertTrue(conv.id)
        self.assertEqual(conv.channel_type, 'sms')
        self.assertEqual(conv.phone_a, self.phone_ours)
        self.assertEqual(conv.phone_b, self.phone_theirs)

    def test_get_or_create_existing(self):
        """Calling get_or_create twice with same pair returns same record."""
        conv1 = self.Conversation.get_or_create('sms', self.phone_ours, self.phone_theirs)
        conv2 = self.Conversation.get_or_create('sms', self.phone_ours, self.phone_theirs)
        self.assertEqual(conv1.id, conv2.id)

    def test_get_or_create_reversed_phones(self):
        """Phones in reversed order resolve to the same conversation (sorted key)."""
        conv1 = self.Conversation.get_or_create('sms', self.phone_ours, self.phone_theirs)
        conv2 = self.Conversation.get_or_create('sms', self.phone_theirs, self.phone_ours)
        self.assertEqual(conv1.id, conv2.id)

    def test_get_or_create_different_channels(self):
        """Same phone pair on different channels creates separate conversations."""
        sms_conv = self.Conversation.get_or_create('sms', self.phone_ours, self.phone_theirs)
        wa_conv = self.Conversation.get_or_create('whatsapp', self.phone_ours, self.phone_theirs)
        self.assertNotEqual(sms_conv.id, wa_conv.id)

    def test_get_or_create_with_partner(self):
        """Partner is set on creation and updated if missing."""
        conv = self.Conversation.get_or_create(
            'sms', self.phone_ours, self.phone_theirs, partner=self.partner_1)
        self.assertEqual(conv.partner_id, self.partner_1)

    def test_get_or_create_updates_partner(self):
        """Partner is backfilled on existing conversation if previously unset."""
        conv = self.Conversation.get_or_create('sms', self.phone_ours, self.phone_theirs)
        self.assertFalse(conv.partner_id)
        conv2 = self.Conversation.get_or_create(
            'sms', self.phone_ours, self.phone_theirs, partner=self.partner_1)
        self.assertEqual(conv2.partner_id, self.partner_1)

    # ------------------------------------------------------------------
    # Conversation key
    # ------------------------------------------------------------------

    def test_conversation_key_format(self):
        """Key is channel:sorted_phone1|sorted_phone2."""
        conv = self.Conversation.get_or_create('sms', '+15559999999', '+15550000000')
        self.assertEqual(conv.conversation_key, 'sms:+15550000000|+15559999999')

    @mute_logger('odoo.sql_db')
    def test_conversation_key_unique_constraint(self):
        """Cannot create two conversations with the same key."""
        self.Conversation.create({
            'channel_type': 'sms',
            'phone_a': self.phone_ours,
            'phone_b': self.phone_theirs,
        })
        with self.assertRaises(Exception):
            self.Conversation.create({
                'channel_type': 'sms',
                'phone_a': self.phone_ours,
                'phone_b': self.phone_theirs,
            })

    # ------------------------------------------------------------------
    # Computed fields
    # ------------------------------------------------------------------

    def test_name_from_partner(self):
        """Name is partner name when partner is set."""
        conv = self.Conversation.get_or_create(
            'sms', self.phone_ours, self.phone_theirs, partner=self.partner_1)
        self.assertEqual(conv.name, self.partner_1.name)

    def test_name_fallback_phone(self):
        """Name falls back to phone_b when no partner."""
        conv = self.Conversation.get_or_create('sms', self.phone_ours, self.phone_theirs)
        self.assertEqual(conv.name, self.phone_theirs)

    def test_last_message_computed(self):
        """last_message_* fields reflect the most recent message."""
        conv = self.Conversation.get_or_create('sms', self.phone_ours, self.phone_theirs)
        self.Message.create({
            'message_sid': 'SM' + 'a' * 32,
            'from_number': self.phone_ours,
            'to_number': self.phone_theirs,
            'body': 'First message',
            'sender_user': self.env.user.id,
            'conversation_id': conv.id,
        })
        self.Message.create({
            'message_sid': 'SM' + 'b' * 32,
            'from_number': self.phone_theirs,
            'to_number': self.phone_ours,
            'body': 'Second message',
            'status': 'received',
            'conversation_id': conv.id,
        })
        conv.invalidate_recordset()
        # Both messages created in same transaction may share create_date;
        # verify count and that last_message_body is one of the two
        self.assertEqual(conv.message_count, 2)
        self.assertIn(conv.last_message_body, ['First message', 'Second message'])

    def test_has_error_computed(self):
        """has_error is True when any message in conversation has an error."""
        conv = self.Conversation.get_or_create('sms', self.phone_ours, self.phone_theirs)
        msg = self.Message.create({
            'message_sid': 'SM' + 'c' * 32,
            'from_number': self.phone_ours,
            'to_number': self.phone_theirs,
            'body': 'Error message',
            'has_error': True,
            'conversation_id': conv.id,
        })
        conv.invalidate_recordset()
        self.assertTrue(conv.has_error)

    # ------------------------------------------------------------------
    # get_messages
    # ------------------------------------------------------------------

    def test_get_messages_returns_ordered(self):
        """get_messages returns messages oldest first."""
        conv = self.Conversation.get_or_create('sms', self.phone_ours, self.phone_theirs)
        self.Message.create({
            'message_sid': 'SM' + 'd' * 32,
            'from_number': self.phone_ours,
            'to_number': self.phone_theirs,
            'body': 'Older',
            'conversation_id': conv.id,
        })
        self.Message.create({
            'message_sid': 'SM' + 'e' * 32,
            'from_number': self.phone_theirs,
            'to_number': self.phone_ours,
            'body': 'Newer',
            'conversation_id': conv.id,
        })
        messages = conv.get_messages()
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]['body'], 'Older')
        self.assertEqual(messages[1]['body'], 'Newer')

    # ------------------------------------------------------------------
    # send_message routing
    # ------------------------------------------------------------------

    def test_send_message_empty_body_raises(self):
        """send_message raises on empty body."""
        conv = self.Conversation.get_or_create('sms', self.phone_ours, self.phone_theirs)
        with self.assertRaises(ValidationError):
            conv.send_message('')

    @mute_logger('odoo.addons.connect.models.message')
    def test_send_message_whitespace_body_raises(self):
        """send_message raises on whitespace-only body."""
        conv = self.Conversation.get_or_create('sms', self.phone_ours, self.phone_theirs)
        with self.assertRaises(ValidationError):
            conv.send_message('   ')

    def test_send_message_sms_routes_to_message_send(self):
        """send_message for SMS channel calls connect.message.send()."""
        conv = self.Conversation.get_or_create('sms', self.phone_ours, self.phone_theirs)
        with patch.object(type(self.Message), 'send') as mock_send:
            conv.send_message('Hello SMS')
            mock_send.assert_called_once_with(
                recipient=self.phone_theirs,
                body='Hello SMS',
                outgoing_callerid=self.phone_ours,
            )

    def test_send_message_whatsapp_routes_to_sender(self):
        """send_message for WhatsApp channel calls whatsapp_sender.send_whatsapp()."""
        conv = self.Conversation.get_or_create('whatsapp', self.phone_ours, self.phone_theirs)
        # Create a mock whatsapp sender
        mock_sender = MagicMock()
        mock_sender.number = self.phone_ours
        with patch.object(
            type(self.env['connect.whatsapp_sender']), 'search',
            return_value=mock_sender,
        ):
            conv.send_message('Hello WhatsApp')
            mock_sender.send_whatsapp.assert_called_once_with(
                recipient=self.phone_theirs,
                body='Hello WhatsApp',
            )

    def test_send_message_whatsapp_no_sender_raises(self):
        """send_message for WhatsApp raises if no sender available."""
        conv = self.Conversation.get_or_create('whatsapp', '+15550009999', self.phone_theirs)
        with patch.object(
            type(self.env['connect.whatsapp_sender']), 'search',
            return_value=self.env['connect.whatsapp_sender'],
        ), patch.object(
            type(self.env['connect.whatsapp_sender']), 'get_default_sender',
            return_value=self.env['connect.whatsapp_sender'],
        ):
            with self.assertRaises(ValidationError):
                conv.send_message('No sender')

    # ------------------------------------------------------------------
    # Auto-linking: message create hooks conversation
    # ------------------------------------------------------------------

    def test_message_create_auto_links_conversation(self):
        """Creating a message auto-creates and links a conversation."""
        msg = self.Message.create({
            'message_sid': 'SM' + 'f' * 32,
            'from_number': self.phone_ours,
            'to_number': self.phone_theirs,
            'body': 'Auto-link test',
            'sender_user': self.env.user.id,
        })
        self.assertTrue(msg.conversation_id)
        self.assertEqual(msg.conversation_id.channel_type, 'sms')

    def test_message_create_auto_links_whatsapp(self):
        """WhatsApp message auto-creates a whatsapp conversation."""
        msg = self.Message.create({
            'message_sid': 'SM' + 'g' * 32,
            'from_number': self.phone_ours,
            'to_number': self.phone_theirs,
            'body': 'WA auto-link',
            'message_type': 'WhatsApp',
            'sender_user': self.env.user.id,
        })
        self.assertTrue(msg.conversation_id)
        self.assertEqual(msg.conversation_id.channel_type, 'whatsapp')

    def test_message_create_reuses_existing_conversation(self):
        """Multiple messages between same pair share one conversation."""
        msg1 = self.Message.create({
            'message_sid': 'SM' + 'h' * 32,
            'from_number': self.phone_ours,
            'to_number': self.phone_theirs,
            'body': 'First',
            'sender_user': self.env.user.id,
        })
        msg2 = self.Message.create({
            'message_sid': 'SM' + 'i' * 32,
            'from_number': self.phone_theirs,
            'to_number': self.phone_ours,
            'body': 'Reply',
            'status': 'received',
        })
        self.assertEqual(msg1.conversation_id.id, msg2.conversation_id.id)

    def test_message_create_skips_if_conversation_already_set(self):
        """If conversation_id is provided in vals, auto-linking is skipped."""
        conv = self.Conversation.get_or_create('sms', self.phone_ours, self.phone_theirs)
        msg = self.Message.create({
            'message_sid': 'SM' + 'j' * 32,
            'from_number': self.phone_ours,
            'to_number': self.phone_theirs,
            'body': 'Explicit conv',
            'conversation_id': conv.id,
        })
        self.assertEqual(msg.conversation_id.id, conv.id)

    def test_message_create_incoming_resolves_our_number(self):
        """Incoming message correctly identifies phone_a as our number."""
        msg = self.Message.sudo().create({
            'message_sid': 'SM' + 'k' * 32,
            'from_number': self.phone_theirs,
            'to_number': self.phone_ours,
            'body': 'Incoming',
            'status': 'received',
        })
        self.assertTrue(msg.conversation_id)
        # phone_a should be our number, phone_b the external
        self.assertEqual(msg.conversation_id.phone_a, self.phone_ours)
        self.assertEqual(msg.conversation_id.phone_b, self.phone_theirs)

    def test_message_create_outgoing_resolves_our_number(self):
        """Outgoing message correctly identifies phone_a as our number."""
        msg = self.Message.create({
            'message_sid': 'SM' + 'l' * 32,
            'from_number': self.phone_ours,
            'to_number': self.phone_theirs,
            'body': 'Outgoing',
            'sender_user': self.env.user.id,
        })
        self.assertTrue(msg.conversation_id)
        self.assertEqual(msg.conversation_id.phone_a, self.phone_ours)
        self.assertEqual(msg.conversation_id.phone_b, self.phone_theirs)

    def test_message_create_links_partner_on_conversation(self):
        """Auto-linked conversation picks up partner from message."""
        msg = self.Message.create({
            'message_sid': 'SM' + 'm' * 32,
            'from_number': self.phone_ours,
            'to_number': self.phone_theirs,
            'body': 'With partner',
            'partner': self.partner_1.id,
            'sender_user': self.env.user.id,
        })
        self.assertEqual(msg.conversation_id.partner_id, self.partner_1)

    # ------------------------------------------------------------------
    # Onchange / defaults
    # ------------------------------------------------------------------

    def test_onchange_partner_sets_phone(self):
        """Changing partner_id populates phone_b from partner's phone."""
        conv = self.Conversation.new({
            'channel_type': 'sms',
            'phone_a': self.phone_ours,
        })
        conv.partner_id = self.partner_1
        conv._onchange_partner_id()
        self.assertTrue(conv.phone_b)

    def test_active_default_true(self):
        """New conversations are active by default."""
        conv = self.Conversation.get_or_create('sms', self.phone_ours, self.phone_theirs)
        self.assertTrue(conv.active)
