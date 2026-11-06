# -*- coding: utf-8 -*-
"""The standard settings screen shows and keeps Connect's live switches."""
from unittest.mock import patch

from lxml import etree

from odoo.tests import HttpCase, new_test_user, tagged

from .common import ConnectTestCase


@tagged('post_install', '-at_install')
class TestConnectSettingsScreen(ConnectTestCase):

    def test_screen_loads_live_value_without_stored_parameter(self):
        """A switch set on connect.settings shows as set on the screen.

        The screen once loaded these switches from config parameters that
        were never written, so it showed them off; saving any app's settings
        then turned call transcription off.
        """
        Settings = self.env['connect.settings']
        Settings.set_param('transcript_calls', True)
        self.env['ir.config_parameter'].sudo().search(
            [('key', '=', 'connect.transcript_calls')]).unlink()

        screen = self.env['res.config.settings'].create({})

        self.assertTrue(screen.connect_transcript_calls)

    def test_saving_screen_keeps_live_value(self):
        Settings = self.env['connect.settings']
        Settings.set_param('transcript_calls', True)
        self.env['ir.config_parameter'].sudo().search(
            [('key', '=', 'connect.transcript_calls')]).unlink()

        self.env['res.config.settings'].create({}).set_values()

        self.assertTrue(Settings.get_param('transcript_calls'))

    def test_saving_screen_writes_change(self):
        Settings = self.env['connect.settings']
        Settings.set_param('transcript_calls', True)

        screen = self.env['res.config.settings'].create({})
        screen.connect_transcript_calls = False
        screen.set_values()

        self.assertFalse(Settings.get_param('transcript_calls'))

    def test_general_settings_button_opens_the_general_form(self):
        view = self.env.ref('connect.res_config_settings_view_form_connect')
        action = self.env.ref('connect.connect_settings_action')
        buttons = etree.fromstring(view.arch_db).xpath(
            "//block[@name='connect_general_settings']//button[@type='action']")
        self.assertEqual([button.get('name') for button in buttons], [str(action.id)])
        opened = action.sudo().run()
        self.assertEqual(opened['res_model'], 'connect.settings')
        self.assertEqual(opened['view_id'], self.env.ref('connect.connect_settings_form').id)

    def test_textable_checkbox_sits_behind_the_gate(self):
        view = self.env.ref('connect.res_config_settings_view_form_connect')
        field = etree.fromstring(view.arch_db).xpath(
            "//block[@name='connect_ee_settings']//field[@name='module_connect_textable']")
        self.assertEqual(len(field), 1)
        self.assertEqual(field[0].get('readonly'), 'not connect_ee_entitled')

    def test_locked_textable_checkbox_changes_nothing(self):
        """Not entitled: ticking it installs nothing, and unticking it removes nothing."""
        Screen = type(self.env['res.config.settings'])
        modules = self.env['ir.module.module'].sudo().search([('name', '=', 'connect_textable')])
        installed = modules.state in ('installed', 'to upgrade', 'to install')
        for ticked in (True, False):
            with self.subTest(ticked=ticked), patch.object(
                    Screen, '_compute_connect_ee_entitled',
                    lambda records: records.update({'connect_ee_entitled': False})):
                screen = self.env['res.config.settings'].create({})
                screen.module_connect_textable = ticked
                self.assertFalse(screen.connect_ee_entitled)
                screen.set_values()
                self.assertEqual(screen.module_connect_textable, installed)


@tagged('post_install', '-at_install')
class TestConnectSettingsScreenBrowser(HttpCase):

    def test_general_settings_button_opens_in_a_browser(self):
        new_test_user(self.env, login='connect_settings_admin',
                      groups='base.group_user,base.group_system,connect.group_admin')
        self.start_tour('/odoo/connect-settings',
                        'connect_general_settings_button', login='connect_settings_admin')
