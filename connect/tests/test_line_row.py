# -*- coding: utf-8 -*-
"""The From row on a real page, for a person with two lines, from their real rows.

The mint is patched and the transports are the test bundle's loopback, so no call
leaves the browser. Whether a real provider's audio works is not proven here.
"""

from odoo import api
from odoo.tests import HttpCase, tagged

from .test_phone_lines import patch_line_options


@tagged('post_install', '-at_install')
class TestLineRow(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = cls.env.ref('base.user_admin')
        # A cold load_menus holds the one test cursor past the lock timeout
        # while the browser fetches assets, so warm it outside the browser.
        cls.env['ir.ui.menu'].with_user(cls.admin).load_web_menus('')

    def setUp(self):
        super().setUp()
        patch_line_options(self)
        ConnectUser = self.env['connect.user'].with_context(
            no_clear_cache=True, no_twilio_create=True)
        connect_user = ConnectUser.search([('user', '=', self.admin.id)], limit=1)
        connect_user = connect_user or ConnectUser.create({'user': self.admin.id})
        self.env['connect.user.tool'].search([('user_id', '=', connect_user.id)]).unlink()
        Tool = self.env['connect.tool']
        self.desk = Tool.create({'name': 'Desk', 'adapter': 'twilio'})
        self.sales = Tool.create({'name': 'Sales plan', 'adapter': 'other_voice'})
        for tool, sequence in ((self.desk, 10), (self.sales, 20)):
            self.env['connect.user.tool'].create({
                'user_id': connect_user.id,
                'tool_id': tool.id,
                'role': 'active',
                'use': 'voice',
                'sequence': sequence,
            })
        self.mints = []
        case = self

        @api.model
        def get_client_token(model, nonce=False):
            tool_id = model.env.context.get('connect_tool_id')
            case.mints.append((tool_id, nonce))
            if tool_id == case.sales.id:
                return {'token': 'sales', 'caller_ids': ['+15550101', '+15550102']}
            return {'token': 'desk', 'outgoing_callerid': '+15550100'}

        self.patch(type(self.env['connect.user']), 'get_client_token', get_client_token)

    def test_tour(self):
        self.start_tour('/odoo', 'connect_line_row_tour', login=self.admin.login)
        # The tour loads the page twice, and each load mints once per line.
        self.assertEqual(
            sorted(tool_id for tool_id, _nonce in self.mints),
            sorted([self.desk.id, self.sales.id] * 2))
        self.assertTrue(all(nonce for _tool_id, nonce in self.mints))
