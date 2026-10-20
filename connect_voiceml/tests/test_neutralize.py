# -*- coding: utf-8 -*-
"""Neutralization blanks VoiceML credentials."""
from odoo.tests import TransactionCase, tagged
from odoo.tools.misc import file_open


@tagged('post_install', '-at_install')
class TestVoicemlNeutralize(TransactionCase):

    def test_neutralize_blanks_secrets(self):
        icp = self.env['ir.config_parameter'].sudo()
        icp.set_param('connect.voiceml_api_key', 'secret')
        icp.set_param('connect.display_voiceml_api_key', 'secret')
        icp.set_param('connect.voiceml_auto_sync', 'True')
        settings = self.env['connect.settings'].sudo().search([], limit=1) \
            or self.env['connect.settings'].sudo().create({})
        settings.write({'voiceml_api_key': 'secret',
                        'display_voiceml_api_key': 'secret',
                        'voiceml_account_sid': 'ACkeep'})
        user = self.env['connect.user'].sudo().search([], limit=1)
        if user:
            user.with_context(skip_voiceml_sync=True).write(
                {'voiceml_password': 'secret'})
        self.env.flush_all()

        with file_open('connect_voiceml/data/neutralize.sql') as f:
            self.env.cr.execute(f.read())
        self.env.invalidate_all()

        self.assertFalse(icp.get_param('connect.voiceml_api_key'))
        self.assertFalse(icp.get_param('connect.display_voiceml_api_key'))
        self.assertEqual(icp.get_param('connect.voiceml_auto_sync'), 'False')
        self.assertFalse(settings.voiceml_api_key)
        self.assertFalse(settings.display_voiceml_api_key)
        self.assertFalse(settings.voiceml_auto_sync)
        self.assertEqual(settings.voiceml_account_sid, 'ACkeep')
        if user:
            self.assertFalse(user.voiceml_password)
