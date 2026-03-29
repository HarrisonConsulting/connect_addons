# -*- coding: utf-8 -*-
"""Tests for credential sync from connect.settings to res.company."""

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCredentialSync(TransactionCase):
    """Test that Twilio credentials flow from connect.settings to res.company."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Settings = cls.env['connect.settings']
        cls.company = cls.env.company
        # Ensure a connect.settings record exists
        cls.Settings.get_param('account_sid')

    def _get_settings_record(self):
        return self.Settings.sudo().search([], limit=1)

    def test_sync_on_account_sid_write(self):
        """Writing account_sid to connect.settings syncs to company."""
        self.company.sudo().write({'sms_provider': 'twilio'})

        test_sid = 'AC' + 'a' * 32
        rec = self._get_settings_record()
        # Also set auth_token so the sync condition (both present) is met
        rec.with_context(skip_protected_fields=True).write({
            'auth_token': 'z' * 32,
        })
        rec.write({'account_sid': test_sid})

        self.assertEqual(
            self.company.sudo().sms_twilio_account_sid,
            test_sid,
            "account_sid should sync to res.company",
        )

    def test_sync_on_protected_auth_token(self):
        """Writing auth_token via protected field dance syncs to company."""
        self.company.sudo().write({'sms_provider': 'twilio'})

        rec = self._get_settings_record()
        # Set account_sid first so both are present
        rec.write({'account_sid': 'AC' + 'd' * 32})

        test_token = 'x' * 32
        rec.with_context(skip_protected_fields=True).write({
            'auth_token': test_token,
        })

        self.assertEqual(
            self.company.sudo().sms_twilio_auth_token,
            test_token,
            "auth_token should sync to res.company via protected field dance",
        )

    def test_no_sync_without_twilio_provider(self):
        """Companies not using twilio provider are not updated."""
        self.company.sudo().write({
            'sms_provider': 'iap',
            'sms_twilio_account_sid': False,
        })

        rec = self._get_settings_record()
        rec.with_context(skip_protected_fields=True).write({
            'auth_token': 'a' * 32,
        })
        rec.write({'account_sid': 'AC' + 'b' * 32})

        self.assertFalse(
            self.company.sudo().sms_twilio_account_sid,
            "IAP company should not receive Twilio credentials",
        )

    def test_post_init_hook_sets_provider(self):
        """post_init_hook sets sms_provider to 'twilio' when credentials exist."""
        rec = self._get_settings_record()
        rec.write({'account_sid': 'AC' + 'c' * 32})
        rec.with_context(skip_protected_fields=True).write({
            'auth_token': 'y' * 32,
        })

        # Reset company to non-twilio state
        self.company.sudo().write({
            'sms_provider': 'iap',
            'sms_twilio_account_sid': False,
            'sms_twilio_auth_token': False,
        })

        from odoo.addons.connect_sms.hooks import _post_init_hook
        _post_init_hook(self.env)

        self.assertEqual(self.company.sudo().sms_provider, 'twilio')
        self.assertEqual(self.company.sudo().sms_twilio_account_sid, 'AC' + 'c' * 32)
