# -*- coding: utf-8 -*-
"""Neutralization blanks VoiceTel credentials and stops remote activity."""
from odoo.tests import TransactionCase, tagged
from odoo.tools.misc import file_open


@tagged('post_install', '-at_install')
class TestVoiceTelNeutralize(TransactionCase):

    def test_neutralize_blanks_secrets(self):
        icp = self.env['ir.config_parameter'].sudo()
        icp.set_param('connect.voicetel_api_key', 'secret')
        icp.set_param('connect.display_voicetel_api_key', 'secret')
        icp.set_param('connect.voicetel_auto_sync', 'True')
        icp.set_param('connect.voicetel_fetch_call_prices', 'True')
        settings = self.env['connect.settings'].sudo().search([], limit=1) \
            or self.env['connect.settings'].sudo().create({})
        settings.with_context(skip_protected_fields=True, skip_icp_sync=True).write({
            'voicetel_api_key': 'secret', 'display_voicetel_api_key': 'secret',
            'voicetel_account_sid': 'ACkeep'})
        odoo_user = self.env['res.users'].create({'name': 'N', 'login': 'vt_neutralize'})
        user = self.env['connect.user'].sudo().with_context(
            no_voicetel_create=True).create({'user': odoo_user.id})
        user.with_context(skip_voicetel_sync=True).write({'voicetel_password': 'secret'})
        cron = self.env.ref('connect_voicetel.voicetel_fetch_call_prices')
        cron.active = True
        self.env.cr.execute(
            "CREATE TABLE connect_voicetel_legacy_neutral (id int, sip_password varchar,"
            " friendly_name varchar);"
            "INSERT INTO connect_voicetel_legacy_neutral VALUES (1, 'pw', 'keep')")
        self.env.flush_all()

        with file_open('connect_voicetel/data/neutralize.sql') as f:
            self.env.cr.execute(f.read())
        self.env.invalidate_all()

        self.assertFalse(icp.get_param('connect.voicetel_api_key'))
        self.assertFalse(icp.get_param('connect.display_voicetel_api_key'))
        self.assertEqual(icp.get_param('connect.voicetel_auto_sync'), 'False')
        self.assertEqual(icp.get_param('connect.voicetel_fetch_call_prices'), 'False')
        self.assertFalse(settings.voicetel_api_key)
        self.assertFalse(settings.display_voicetel_api_key)
        self.assertFalse(settings.voicetel_auto_sync)
        self.assertEqual(settings.voicetel_account_sid, 'ACkeep')
        self.assertFalse(user.voicetel_password)
        self.assertFalse(cron.active)
        self.env.cr.execute("SELECT sip_password, friendly_name FROM connect_voicetel_legacy_neutral")
        self.assertEqual(self.env.cr.fetchone(), (None, 'keep'))
