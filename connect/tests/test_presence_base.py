# -*- coding: utf-8 -*-
"""The softphone reports presence, whatever provider modules are installed."""
from unittest.mock import patch

from odoo.addons.connect.models.user import User
from odoo.service.model import call_kw
from odoo.tests import TransactionCase, new_test_user, tagged

OVERRIDING_MODULES = ('connect_pbx', 'connect_enqueue')


@tagged('post_install', '-at_install')
class TestPresenceBase(TransactionCase):

    def test_base_method_writes_nothing(self):
        model = self.env['connect.user']
        with patch.object(type(model), 'write') as write, patch.object(type(model), 'create') as create:
            self.assertIs(User.update_presence(model, 'available'), True)
        write.assert_not_called()
        create.assert_not_called()

    def test_softphone_can_call_it_by_rpc(self):
        """The call the panel makes, from a user with no Connect user row."""
        user = new_test_user(self.env, login='presence_base_user', groups='connect.group_user')
        model = self.env['connect.user'].with_user(user)
        self.assertIs(call_kw(model, 'update_presence', ['available'], {}), True)

    def test_a_module_that_keeps_presence_still_wins(self):
        installed = self.env['ir.module.module'].search_count(
            [('name', 'in', OVERRIDING_MODULES), ('state', '=', 'installed')])
        resolved = type(self.env['connect.user']).update_presence
        if installed:
            self.assertIsNot(resolved, User.update_presence)
        else:
            self.assertIs(resolved, User.update_presence)
