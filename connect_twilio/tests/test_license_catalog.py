# -*- coding: utf-8 -*-
"""Twilio is covered by the connect license."""
from odoo.tests import TransactionCase, tagged

from odoo.addons.connect.models.license import ODUIST_MODULES


@tagged('post_install', '-at_install')
class TestLicenseCatalog(TransactionCase):
    def test_twilio_module_is_not_a_catalog_product(self):
        """Buy All and the navbar banner only name products the server sells."""
        self.assertIn('connect', ODUIST_MODULES)
        self.assertNotIn('connect_twilio', ODUIST_MODULES)
        self.assertNotIn('connect_voicetel', ODUIST_MODULES)
