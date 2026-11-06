# -*- coding: utf-8 -*-
"""Freeze: the exact payload a Twilio softphone mounts from.

Written before lines existed, and not edited since. A key added here reaches
every one-line user's browser.
"""

from odoo.tests import new_test_user, tagged

from .common import TwilioTestCommon


@tagged('post_install', '-at_install')
class TestSingleLineTokenKeys(TwilioTestCommon):

    def test_success_path_returns_exactly_six_keys(self):
        Settings = self.env['connect.settings'].sudo()
        Settings.set_param('account_sid', 'AC' + '0' * 32)
        Settings.set_param('twilio_api_key', 'SK' + '0' * 32)
        Settings.set_param('twilio_api_secret', 's' * 32)
        domain = self.env['connect.twilio.domain'].with_context(
            no_twilio_create=True,
        ).create({
            'friendly_name': 'Token Keys',
            'subdomain': 'tokenkeys',
        })
        odoo_user = new_test_user(
            self.env, login='single-line-token-keys', groups='base.group_user,connect.group_user')
        self.env['connect.user'].with_context(
            no_clear_cache=True, no_twilio_create=True,
        ).create({
            'user': odoo_user.id,
            'username': 'tokenkeys',
            'domain': domain.id,
            'client_enabled': True,
            'sip_enabled': False,
            'originate_provider': 'twilio',
        })

        data = self.env['connect.user'].with_user(odoo_user).get_client_token()

        self.assertEqual(set(data), {
            'token',
            'first_callflow',
            'edge',
            'record_calls',
            'exten',
            'outgoing_callerid',
        })
        self.assertTrue(data['token'])
