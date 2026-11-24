# -*- coding: utf-8 -*-
"""Dial suggestions beside the keypad, on a real page, for a person with one line and for one with two.

The mint is patched and the transports are the test bundle's loopback, so no call
leaves the browser.
"""

from odoo.tests import tagged

from . import test_line_row


@tagged('post_install', '-at_install')
class TestDialSuggestions(test_line_row.TestLineRow):

    def setUp(self):
        super().setUp()
        self.env['res.partner'].create([
            {'name': 'Zyxwvut Quillfeather', 'phone': '+1 555 0147'},
            {'name': 'Zyxwvut Plumeweaver', 'phone': '+1 555 0148'},
        ])

    def test_tour(self):
        self.start_tour('/odoo', 'connect_dial_suggestions_tour', login=self.admin.login)

    def test_tour_one_line(self):
        self.env['connect.user.tool'].search([('tool_id', '=', self.sales.id)]).unlink()
        self.start_tour('/odoo', 'connect_dial_suggestions_one_line_tour', login=self.admin.login)
