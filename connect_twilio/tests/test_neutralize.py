# -*- coding: utf-8 -*-
"""Neutralization blanks Twilio SIP passwords."""
from odoo.tests import TransactionCase, tagged
from odoo.tools.misc import file_open


@tagged('post_install', '-at_install')
class TestTwilioNeutralize(TransactionCase):

    def test_neutralize_blanks_sip_password(self):
        user = self.env['connect.user'].sudo().search([], limit=1)
        if not user:
            self.skipTest('no connect.user to check')
        self.env.cr.execute(
            "UPDATE connect_user SET password = 'secret' WHERE id = %s",
            [user.id])
        self.env.invalidate_all()

        with file_open('connect_twilio/data/neutralize.sql') as f:
            self.env.cr.execute(f.read())
        self.env.cr.execute(
            "SELECT count(*) FROM connect_user WHERE password IS NOT NULL")
        self.assertEqual(self.env.cr.fetchone()[0], 0)
