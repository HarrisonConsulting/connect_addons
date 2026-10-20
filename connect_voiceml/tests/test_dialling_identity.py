# -*- coding: utf-8 -*-
from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestDiallingIdentity(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Settings = self.env['connect.settings']
        self.Settings.set_param('api_url', 'https://odoo.example.com/')
        self.Settings.set_param('call_duration_limit', '3600')
        self.env.company.country_id = self.env.ref('base.us')
        self.OCID = self.env['connect.voiceml.outgoing_callerid']
        self.OCID.search([]).write({'is_default': False})
        res_user = self.env['res.users'].create({'name': 'Dial', 'login': 'dial_user'})
        self.user = self.env['connect.user'].create({
            'user': res_user.id, 'name': 'Dial', 'voiceml_username': 'dial1'})

    def _ocid(self, number, **vals):
        return self.OCID.create(dict({'number': number, 'friendly_name': number}, **vals))

    def test_pstn_caller_id_prefers_user_then_default_never_extension(self):
        self.assertEqual(self.user.voiceml_pstn_caller_id(), '')
        self._ocid('+15550000001', is_default=True)
        self.assertEqual(self.user.voiceml_pstn_caller_id(), '+15550000001')
        own = self._ocid('+15550000002')
        self.user.voiceml_outgoing_callerid = own
        self.assertEqual(self.user.voiceml_pstn_caller_id(), '+15550000002')
        exten = self.env['connect.voiceml.exten'].create({'number': '1234'})
        self.user.with_context(skip_voiceml_sync=True).voiceml_exten = exten
        self.assertEqual(self.user.voiceml_pstn_caller_id(), '+15550000002')

    def test_ten_digit_number_gets_us_prefix(self):
        self.assertEqual(self.Settings._voiceml_dial_number('4845551234'), '+14845551234')
        self.assertEqual(self.Settings._voiceml_dial_number('(484) 555-1234'), '+14845551234')
        self.assertEqual(self.Settings._voiceml_dial_number('+14845551234'), '+14845551234')
        self.assertEqual(self.Settings._voiceml_dial_number('14845551234'), '+14845551234')

    def test_desk_phone_pstn_twiml_shows_ocid(self):
        self._ocid('+15550000001', is_default=True)
        twiml = self.Settings.get_voiceml_external_call_route(
            self.Settings._voiceml_dial_number('4845551234'),
            self.user.voiceml_pstn_caller_id(), 'https://x/status')
        self.assertIn('+15550000001', twiml)
        self.assertIn('+14845551234', twiml)

    def test_originate_without_caller_id_raises_before_rest(self):
        client = MagicMock()
        with patch.object(type(self.Settings), '_voiceml_client', return_value=client), \
                patch.object(type(self.Settings), '_get_originate_provider', return_value='voiceml'):
            with self.assertRaises(UserError):
                self.Settings.originate_call(
                    '4845551234', user=self.env['res.users'].browse(self.user.user.id))
        self.assertEqual(client.mock_calls, [])

    def test_originate_dials_e164_with_ocid(self):
        self._ocid('+15550000001', is_default=True)
        client = MagicMock()
        client.calls.create.return_value.sid = 'CAx'
        with patch.object(type(self.Settings), '_voiceml_client', return_value=client), \
                patch.object(type(self.Settings), '_get_originate_provider', return_value='voiceml'):
            self.Settings.originate_call(
                '4845551234', user=self.env['res.users'].browse(self.user.user.id))
        request = client.calls.create.call_args[0][0]
        self.assertEqual(request.from_, '+15550000001')
        self.assertIn('+14845551234', request.twiml)
