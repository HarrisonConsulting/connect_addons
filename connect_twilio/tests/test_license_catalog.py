# -*- coding: utf-8 -*-
"""Twilio is its own catalog product."""
from odoo.tests import TransactionCase, tagged

from odoo.addons.connect.models.license import ODUIST_MODULES


@tagged('post_install', '-at_install')
class TestLicenseCatalog(TransactionCase):
    def test_twilio_module_is_a_catalog_product(self):
        """The license form offers the Twilio module the product page sells."""
        self.assertIn('connect', ODUIST_MODULES)
        self.assertIn('connect_twilio', ODUIST_MODULES)
        self.assertNotIn('connect_voicetel', ODUIST_MODULES)
