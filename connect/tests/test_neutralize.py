# -*- coding: utf-8 -*-
"""Neutralization blanks core Connect provider secrets."""
from odoo.tests import TransactionCase, tagged
from odoo.tools.misc import file_open

KEYS = (
    'connect.auth_token', 'connect.region_auth_token',
    'connect.twilio_api_secret', 'connect.openai_api_key',
    'connect.display_auth_token', 'connect.display_region_auth_token',
    'connect.display_twilio_api_secret', 'connect.display_openai_api_key',
    'connect.voicetel_api_key', 'connect.voicetel_account_sid',
)


@tagged('post_install', '-at_install')
class TestConnectNeutralize(TransactionCase):

    def test_neutralize_blanks_secrets(self):
        icp = self.env['ir.config_parameter'].sudo()
        for key in KEYS:
            icp.set_param(key, 'secret')
        self.env.flush_all()

        with file_open('connect/data/neutralize.sql') as f:
            self.env.cr.execute(f.read())

        for key in KEYS:
            self.assertFalse(icp.get_param(key), key)
