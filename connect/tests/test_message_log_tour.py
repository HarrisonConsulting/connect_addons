# -*- coding: utf-8 -*-
"""Browser cover for the message log list."""

from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestMessageLogTour(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = cls.env.ref('base.user_admin')
        cls.env['ir.ui.menu'].with_user(cls.admin).load_web_menus('')
        Message = cls.env['connect.message']
        Message.create({
            'direction': 'outgoing',
            'status': 'failed',
            'from_number': '+15550001111',
            'to_number': '+15550002222',
            'body': 'This one failed',
        })
        Message.create({
            'direction': 'incoming',
            'status': 'received',
            'from_number': '+15550002222',
            'to_number': '+15550001111',
            'body': 'This one arrived',
        })

    def test_message_log_list_opens(self):
        action = self.env.ref('connect.action_connect_message')
        self.start_tour(
            f'/odoo/action-{action.id}', 'connect_message_log_tour',
            login=self.admin.login)

    def test_message_log_survives_view_change(self):
        """Back from a message after the list view changed under the open tab."""
        view = self.env.ref('connect.view_connect_message_tree')
        # The list as it shipped before the failed-row decoration (8bb744ce).
        view.arch_db = """<list default_order="create_date desc">
            <field name="direction_display" string=" " width="30px"/>
            <field name="status_display" string=" " width="30px"/>
            <field name="from_number"/>
            <field name="to_number"/>
            <field name="body"/>
            <field name="message_type"/>
            <field name="partner"/>
            <field name="create_date" string="Date"/>
        </list>"""
        action = self.env.ref('connect.action_connect_message')
        self.start_tour(
            f'/odoo/action-{action.id}', 'connect_message_log_view_change_tour',
            login=self.admin.login)
