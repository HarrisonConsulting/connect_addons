# -*- coding: utf-8 -*-
from uuid import uuid4

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCallerPayload(TransactionCase):

    def _internal_user(self):
        login = uuid4().hex
        return self.env['res.users'].with_context(
            no_reset_password=True,
            tracking_disable=True,
        ).create({
            'name': login,
            'login': login,
            'share': False,
            'group_ids': [(4, self.env.ref('base.group_user').id)],
        })

    def _account(self, endpoint, tool, user, slot):
        return self.env['connect.endpoint.account'].create({
            'endpoint_id': endpoint.id,
            'tool_id': tool.id,
            'user_id': user.id,
            'slot': slot,
            'name': 'line-%s' % slot,
            'sip_user': uuid4().hex,
        })

    def test_registered_line_choice_and_payload(self):
        user = self.env['connect.user'].with_context(
            no_clear_cache=True,
        ).create({'user': self._internal_user().id})
        other = self.env['connect.user'].with_context(
            no_clear_cache=True,
        ).create({'user': self._internal_user().id})
        tool = self.env['connect.tool'].create({
            'name': 'sip-%s' % uuid4().hex,
            'adapter': 'sip',
        })
        user.write({
            'tool_assignment_ids': [(0, 0, {
                'tool_id': tool.id,
                'role': 'active',
                'sequence': 1,
                'use': 'voice',
            })],
        })
        endpoint = self.env['connect.endpoint'].create({
            'name': 'desk-%s' % uuid4().hex,
        })
        first = self._account(endpoint, tool, user, 1)
        second = self._account(endpoint, tool, user, 2)
        stranger = self._account(endpoint, tool, other, 3)

        first.mark_registered()
        stranger.mark_registered()
        self.assertEqual(user.place_desk_call(), first)
        self.assertNotIn(stranger, user.desk_accounts())
        with self.assertRaises(UserError):
            user.place_desk_call(account=stranger)

        second.mark_registered()
        with self.assertRaises(UserError) as error:
            user.place_desk_call()
        message = str(error.exception)
        self.assertIn(tool.name, message)
        self.assertIn('slot 1', message)
        self.assertIn('slot 2', message)
        self.assertIn('Which line', message)
        self.assertEqual(user.place_desk_call(account=second), second)

        payload = endpoint.caller_payload(first)
        self.assertEqual(payload, {
            'endpoint_id': endpoint.id,
            'endpoint_name': endpoint.name,
            'account_id': first.id,
            'slot': first.slot,
            'tool_id': tool.id,
            'tool_name': tool.name,
            'registration': 'registered',
            'partner': False,
            'actions': [],
        })
