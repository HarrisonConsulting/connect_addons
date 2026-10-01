# -*- coding: utf-8 -*-
"""A person keeps one row and an ordered list of voice systems."""

from uuid import uuid4

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestToolAssignment(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        login = 'tool-assign-%s' % uuid4().hex
        cls.odoo_user = cls.env['res.users'].with_context(
            no_reset_password=True,
        ).create({
            'name': 'Tool Assignment',
            'login': login,
            'email': '%s@example.com' % login,
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })
        cls.connect_user = cls.env['connect.user'].with_context(
            no_clear_cache=True,
        ).create({'user': cls.odoo_user.id})
        cls.tool_a = cls.env['connect.tool'].create({
            'name': 'Desk A',
            'adapter': 'sip',
        })
        cls.tool_b = cls.env['connect.tool'].create({
            'name': 'Desk B',
            'adapter': 'sip',
        })
        cls.row_a = cls.env['connect.user.tool'].create({
            'user_id': cls.connect_user.id,
            'tool_id': cls.tool_a.id,
            'role': 'active',
            'use': 'both',
            'sequence': 10,
        })
        cls.row_b = cls.env['connect.user.tool'].create({
            'user_id': cls.connect_user.id,
            'tool_id': cls.tool_b.id,
            'role': 'active',
            'use': 'both',
            'sequence': 20,
        })

    def test_order_backup_and_reorder(self):
        user = self.connect_user
        self.assertEqual(user.first_tool('voice'), self.tool_a)
        self.assertEqual(user.first_tool('message'), self.tool_a)
        self.assertEqual(
            user.ordered_tools('voice').mapped('tool_id').ids,
            [self.tool_a.id, self.tool_b.id],
        )
        self.assertEqual(
            user.ordered_tools('message').mapped('tool_id').ids,
            [self.tool_a.id, self.tool_b.id],
        )
        # sip is not an originate or message selection key.
        self.assertFalse(user.originate_provider)
        self.assertFalse(user.message_provider)
        self.row_b.write({'role': 'backup'})
        self.assertEqual(user.first_tool('voice'), self.tool_a)
        self.assertEqual(
            user.ordered_tools('voice').mapped('tool_id').ids,
            [self.tool_a.id, self.tool_b.id],
        )
        kept = user.id
        self.row_b.write({'sequence': 5, 'role': 'active'})
        self.assertEqual(user.first_tool('voice'), self.tool_b)
        self.assertEqual(user.id, kept)
        # An active row stays ahead of a backup even when the backup is first.
        self.row_a.write({'role': 'active', 'sequence': 30})
        self.row_b.write({'role': 'backup', 'sequence': 1})
        self.assertEqual(user.first_tool('voice'), self.tool_a)
        self.assertEqual(
            user.ordered_tools('voice').mapped('tool_id').ids,
            [self.tool_a.id, self.tool_b.id],
        )

    def test_sip_context_falls_through_without_guessing(self):
        """A sip system is not a click-to-call key. Zero or one installed
        telephony modules use the existing fallback. Several modules ask
        for a selection instead of guessing.
        """
        settings = self.env['connect.settings'].with_context(
            connect_tool_id=self.tool_a.id,
            connect_campaign_id=2 ** 31 - 1,
        )
        self._assert_falls_through(
            lambda: settings._get_originate_provider(user=self.odoo_user),
            'originate_provider',
            'Select a click-to-call provider',
            'No telephony module is installed.',
        )
        self._assert_falls_through(
            lambda: settings._get_message_provider(user=self.odoo_user),
            'message_provider',
            'Select a messaging provider',
            'No messaging module is installed.',
        )
        phone = self.env['connect.user'].with_user(self.odoo_user).with_context(
            connect_tool_id=self.tool_a.id,
        )._get_phone_provider()
        options = self.env['connect.user']._fields['originate_provider'].get_values(self.env)
        self.assertNotEqual(phone, 'sip')
        if len(options) == 1:
            self.assertEqual(phone, options[0])
        else:
            self.assertFalse(phone)

    def _assert_falls_through(self, getter, field_name, several_text, none_text):
        options = self.env['connect.user']._fields[field_name].get_values(self.env)
        try:
            provider = getter()
        except UserError as err:
            message = str(err)
            if len(options) > 1:
                self.assertIn(several_text, message)
            else:
                self.assertEqual(options, [])
                self.assertIn(none_text, message)
        else:
            self.assertNotEqual(provider, 'sip')
            self.assertEqual(options, [provider])

    def test_mirror_writes_twilio_when_that_key_is_installed(self):
        # twilio is not a base selection key. Assert the mirror only when
        # the module that adds it is already installed.
        originate_values = self.env['connect.user']._fields['originate_provider'].get_values(self.env)
        adapter_values = self.env['connect.tool']._fields['adapter'].get_values(self.env)
        if 'twilio' in originate_values and 'twilio' in adapter_values:
            twilio_tool = self.env['connect.tool'].create({
                'name': 'Twilio line',
                'adapter': 'twilio',
            })
            self.env['connect.user.tool'].create({
                'user_id': self.connect_user.id,
                'tool_id': twilio_tool.id,
                'role': 'active',
                'use': 'both',
                'sequence': 1,
            })
            self.assertEqual(self.connect_user.originate_provider, 'twilio')
            self.assertEqual(self.connect_user.first_tool('voice'), twilio_tool)
            self.assertEqual(
                self.env['connect.settings'].with_context(
                    connect_tool_id=twilio_tool.id,
                )._get_originate_provider(user=self.odoo_user),
                'twilio',
            )
            message_values = self.env['connect.user']._fields['message_provider'].get_values(self.env)
            if 'twilio' in message_values:
                self.assertEqual(self.connect_user.message_provider, 'twilio')

    def test_call_stamps_context_tool_and_keeps_an_explicit_one(self):
        stamped = self.env['connect.call'].with_context(
            connect_tool_id=self.tool_a.id,
        ).create({})
        self.assertEqual(stamped.tool_id, self.tool_a)
        explicit = self.env['connect.call'].with_context(
            connect_tool_id=self.tool_a.id,
        ).create({'tool_id': self.tool_b.id})
        self.assertEqual(explicit.tool_id, self.tool_b)
        plain = self.env['connect.call'].create({})
        self.assertFalse(plain.tool_id)
