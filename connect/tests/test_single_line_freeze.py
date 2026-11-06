# -*- coding: utf-8 -*-
"""Freeze: what a person with zero or one line has today.

Written against the phone before lines existed, and not edited since. If one of
these has to change, one-line users have been changed.
"""

from uuid import uuid4

from odoo import api
from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestSingleLineFreeze(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = cls.env.ref('base.user_admin')
        # A cold load_menus holds the one test cursor past the lock timeout
        # while the browser fetches assets, so warm it outside the browser.
        cls.env['ir.ui.menu'].with_user(cls.admin).load_web_menus('')

    def setUp(self):
        super().setUp()
        self.mints = []
        mints = self.mints

        # A database with connect alone mints no credential, and the phone does
        # not mount without one. The test bundle's loopback transport takes this
        # token and never presents it to anyone.
        @api.model
        def get_client_token(model, nonce=False):
            mints.append(nonce)
            return {'token': 'single-line-freeze-token', 'edge': 'ashburn'}

        @api.model
        def _get_phone_provider(model):
            return 'twilio'

        ConnectUser = type(self.env['connect.user'])
        self.patch(ConnectUser, 'get_client_token', get_client_token)
        self.patch(ConnectUser, '_get_phone_provider', _get_phone_provider)

    def _connect_user(self):
        login = 'single-line-%s' % uuid4().hex
        odoo_user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Single Line',
            'login': login,
            'email': '%s@example.com' % login,
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        return self.env['connect.user'].with_context(
            no_clear_cache=True, no_twilio_create=True,
        ).create({'user': odoo_user.id})

    def _row(self, connect_user, tool, **values):
        return self.env['connect.user.tool'].create({
            'user_id': connect_user.id,
            'tool_id': tool.id,
            'role': 'active',
            'use': 'voice',
            **values,
        })

    def test_tour_one_mint_one_panel(self):
        self.start_tour('/odoo', 'single_line_freeze_tour', login=self.admin.login)
        self.assertEqual(len(self.mints), 1, 'The page mints once, at mount.')
        self.assertTrue(self.mints[0], 'The mint carries the tab nonce.')

    def test_zero_rows(self):
        user = self._connect_user()
        for use in ('voice', 'message'):
            self.assertFalse(user.ordered_tools(use))
            self.assertFalse(user.first_tool(use))
        user._sync_provider_mirror()
        self.assertFalse(user.originate_provider)
        self.assertFalse(user.message_provider)

    def test_one_row(self):
        user = self._connect_user()
        tool = self.env['connect.tool'].create({'name': 'Desk', 'adapter': 'sip'})
        row = self._row(user, tool)
        self.assertEqual(user.ordered_tools('voice'), row)
        self.assertEqual(user.first_tool('voice'), tool)
        self.assertFalse(user.ordered_tools('message'))
        self.assertFalse(user.first_tool('message'))
        row.use = 'both'
        self.assertEqual(user.ordered_tools('message'), row)
        self.assertEqual(user.first_tool('message'), tool)

    def test_one_row_mirror_stores_no_key_for_a_non_selection_adapter(self):
        user = self._connect_user()
        tool = self.env['connect.tool'].create({'name': 'Desk', 'adapter': 'sip'})
        self._row(user, tool, use='both')
        self.assertFalse(user.originate_provider)
        self.assertFalse(user.message_provider)

    def test_one_row_mirror_follows_a_selection_adapter(self):
        """The first active row is mirrored and removing it clears the mirror.

        Runs against whichever provider is installed. Connect alone has none,
        and then the case above is all there is to say.
        """
        adapters = self.env['connect.tool']._fields['adapter'].get_values(self.env)
        options = self.env['connect.user']._fields['originate_provider'].get_values(self.env)
        for key in set(adapters) & set(options):
            with self.subTest(key=key):
                user = self._connect_user()
                tool = self.env['connect.tool'].create({'name': key, 'adapter': key})
                row = self._row(user, tool)
                self.assertEqual(user.originate_provider, key)
                self.assertFalse(user.message_provider)
                row.unlink()
                self.assertFalse(user.originate_provider)
