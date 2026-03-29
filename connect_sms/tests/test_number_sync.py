# -*- coding: utf-8 -*-
"""Tests for number sync from connect.outgoing_callerid to sms.twilio.number."""

from unittest.mock import patch
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestNumberSync(TransactionCase):
    """Test that DID numbers sync from connect.outgoing_callerid to sms.twilio.number."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.CallerID = cls.env['connect.outgoing_callerid']
        cls.TwilioNumber = cls.env['sms.twilio.number']
        cls.company = cls.env.company
        cls.company.sudo().write({'sms_provider': 'twilio'})
        cls.us_country = cls.env['res.country'].search([('code', '=', 'US')], limit=1)
        # Ensure company has a country for fallback
        if not cls.company.country_id:
            cls.company.sudo().write({'country_id': cls.us_country.id})

    def _create_callerid(self, number, callerid_type='number', **kwargs):
        vals = {
            'number': number,
            'friendly_name': f'Test {number}',
            'callerid_type': callerid_type,
            'sid': kwargs.pop('sid', 'PN' + 'x' * 32),
            **kwargs,
        }
        # Disable Twilio auto-sync to avoid API calls during tests
        self.env['connect.settings'].set_param('twilio_auto_sync', False)
        return self.CallerID.with_context(skip_validation=True).sudo().create(vals)

    def test_did_numbers_sync(self):
        """DID numbers (callerid_type='number') sync to sms.twilio.number."""
        self._create_callerid('+15551234567', callerid_type='number', sid='PN' + 'a' * 32)

        # Mock country code resolution since test numbers may not resolve
        with patch(
            'odoo.addons.connect_sms.models.outgoing_callerid.phone_validation.phone_get_country_code_for_number',
            return_value='US',
        ):
            self.CallerID.sudo()._sync_to_sms_twilio_numbers()

        twilio_nums = self.TwilioNumber.sudo().search([
            ('company_id', '=', self.company.id),
            ('number', '=', '+15551234567'),
        ])
        self.assertEqual(len(twilio_nums), 1, "DID number should be synced")
        self.assertEqual(twilio_nums.country_id, self.us_country)

    def test_callerid_type_not_synced(self):
        """Plain CallerIDs (callerid_type='outgoing_callerid') are NOT synced."""
        self._create_callerid(
            '+15559999999',
            callerid_type='outgoing_callerid',
            sid='PN' + 'b' * 32,
            status='validated',
        )

        with patch(
            'odoo.addons.connect_sms.models.outgoing_callerid.phone_validation.phone_get_country_code_for_number',
            return_value='US',
        ):
            self.CallerID.sudo()._sync_to_sms_twilio_numbers()

        twilio_nums = self.TwilioNumber.sudo().search([
            ('company_id', '=', self.company.id),
            ('number', '=', '+15559999999'),
        ])
        self.assertEqual(len(twilio_nums), 0, "CallerID type should not sync")

    def test_stale_numbers_removed(self):
        """sms.twilio.number records not in connect are removed on sync."""
        self.TwilioNumber.sudo().create({
            'company_id': self.company.id,
            'number': '+15550000000',
            'country_id': self.us_country.id,
        })

        # Sync with no matching callerids — the stale record should be removed
        self.CallerID.sudo()._sync_to_sms_twilio_numbers()

        twilio_nums = self.TwilioNumber.sudo().search([
            ('company_id', '=', self.company.id),
            ('number', '=', '+15550000000'),
        ])
        self.assertEqual(len(twilio_nums), 0, "Stale number should be removed")

    def test_no_duplicates_on_repeated_sync(self):
        """Running sync twice does not create duplicate sms.twilio.number records."""
        self._create_callerid('+15551111111', callerid_type='number', sid='PN' + 'c' * 32)

        with patch(
            'odoo.addons.connect_sms.models.outgoing_callerid.phone_validation.phone_get_country_code_for_number',
            return_value='US',
        ):
            self.CallerID.sudo()._sync_to_sms_twilio_numbers()
            self.CallerID.sudo()._sync_to_sms_twilio_numbers()

        twilio_nums = self.TwilioNumber.sudo().search([
            ('company_id', '=', self.company.id),
            ('number', '=', '+15551111111'),
        ])
        self.assertEqual(len(twilio_nums), 1, "No duplicates after repeated sync")
