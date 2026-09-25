# -*- coding: utf-8 -*-
"""Twilio account credentials stored on ir.config_parameter."""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestTwilioConfigParameter(TransactionCase):

    def test_get_client_uses_parameters_when_columns_are_empty(self):
        settings = self.env['connect.settings'].sudo()
        icp = self.env['ir.config_parameter'].sudo()
        for name in ('account_sid', 'auth_token'):
            settings.with_context(skip_icp_sync=True).set_param(name, False)
            icp.search([('key', '=', 'connect.%s' % name)]).unlink()
        icp.set_param('connect_twilio.account_sid', 'ACfromparam')
        icp.set_param('connect_twilio.auth_token', 'tokenfromparam')
        icp.set_param('connect_twilio.region', 'ie1')
        icp.set_param('connect_twilio.edge', 'dublin')

        self.assertEqual(
            settings._twilio_param('connect_twilio.account_sid', 'account_sid'),
            'ACfromparam',
        )
        self.assertEqual(
            settings._twilio_param('connect_twilio.auth_token', 'auth_token'),
            'tokenfromparam',
        )
        self.assertEqual(
            settings.get_media_auth(
                'https://api.twilio.com/2010-04-01/Accounts/ACfromparam/Recordings/RE1'),
            ('ACfromparam', 'tokenfromparam'),
        )
        with patch('odoo.addons.connect_twilio.models.settings.Client') as Client:
            settings.get_client()
        Client.assert_called_once_with('ACfromparam', 'tokenfromparam')
        client = Client.return_value
        self.assertEqual(client.region, 'ie1')
        self.assertEqual(client.edge, 'dublin')

    def test_twilio_param_falls_back_to_the_column(self):
        settings = self.env['connect.settings'].sudo()
        icp = self.env['ir.config_parameter'].sudo()
        settings.with_context(skip_icp_sync=True).set_param(
            'account_sid', 'ACfromcolumn')
        icp.search([('key', 'in', [
            'connect_twilio.account_sid',
            'connect.account_sid',
        ])]).unlink()

        self.assertEqual(
            settings._twilio_param('connect_twilio.account_sid', 'account_sid'),
            'ACfromcolumn',
        )

    def test_stored_false_boolean_does_not_fall_back(self):
        settings = self.env['connect.settings'].sudo()
        settings.with_context(skip_icp_sync=True).set_param(
            'twilio_verify_requests', True)
        self.env['ir.config_parameter'].sudo().set_param(
            'connect_twilio.verify_requests', 'False')

        self.assertIs(
            settings._twilio_param(
                'connect_twilio.verify_requests', 'twilio_verify_requests'),
            False,
        )

    def test_boolean_one_is_true_and_a_missing_row_uses_the_column(self):
        settings = self.env['connect.settings'].sudo()
        icp = self.env['ir.config_parameter'].sudo()
        settings.with_context(skip_icp_sync=True).set_param(
            'fetch_call_prices', True)
        icp.search([
            ('key', '=', 'connect_twilio.fetch_call_prices'),
        ]).unlink()
        self.assertIs(
            settings._twilio_param(
                'connect_twilio.fetch_call_prices', 'fetch_call_prices'),
            True,
        )
        icp.set_param('connect_twilio.fetch_call_prices', '1')
        self.assertIs(
            settings._twilio_param(
                'connect_twilio.fetch_call_prices', 'fetch_call_prices'),
            True,
        )

    def test_unchecked_boolean_is_stored_as_false(self):
        self.env['res.config.settings'].create({
            'connect_twilio_verify_requests': False,
        }).set_values()

        self.assertEqual(
            self.env['ir.config_parameter'].sudo().get_param(
                'connect_twilio.verify_requests'),
            'False',
        )

    def test_client_credentials_and_log_context_keep_the_secret_private(self):
        """Twilio supplies its credentials; the log context names only the account."""
        settings = self.env['connect.settings'].sudo()
        icp = self.env['ir.config_parameter'].sudo()
        settings.set_param('rest_provider', 'twilio')
        icp.set_param('connect_twilio.account_sid', 'AC123456789')
        icp.set_param('connect_twilio.auth_token', 'test-secret')

        self.assertEqual(
            settings._get_client_credentials(), ('AC123456789', 'test-secret'))
        context = settings._provider_log_context()
        self.assertEqual(context, 'provider=twilio account=AC123456…')
        self.assertNotIn('test-secret', context)
