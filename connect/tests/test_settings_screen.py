# -*- coding: utf-8 -*-
"""The standard settings screen shows and keeps Connect's live switches."""

from odoo.tests import tagged

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
