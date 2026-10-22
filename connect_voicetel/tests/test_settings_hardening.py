# -*- coding: utf-8 -*-
from unittest.mock import MagicMock, patch

from voiceml.exceptions import ApiError, NotFoundError

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestSettingsHardening(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Settings = self.env['connect.settings']
        self.ICP = self.env['ir.config_parameter'].sudo()
        self.ICP.set_param('connect.voicetel_allow_custom_host', False)

    def _write(self, **vals):
        rec = self.Settings.sudo().search([], limit=1) or self.Settings.sudo().create({})
        rec.write(vals)
        rec.flush_recordset()

    def test_api_host_must_be_voicetel(self):
        with self.assertRaises(ValidationError):
            self._write(voicetel_api_host='attacker.example.com')
        with self.assertRaises(ValidationError):
            self._write(voicetel_api_host='voicetel.com.attacker.example')
        self._write(voicetel_api_host='voiceml.voicetel.com')

    def test_sip_suffix_must_be_voice_tel(self):
        with self.assertRaises(ValidationError):
            self._write(voicetel_sip_suffix='sip.attacker.example')
        self._write(voicetel_sip_suffix='vml.voice.tel')

    def test_custom_host_needs_the_system_parameter(self):
        self.ICP.set_param('connect.voicetel_allow_custom_host', '1')
        self._write(voicetel_api_host='voiceml.example.net')
        self.assertEqual(self.Settings._voicetel_api_host(), 'voiceml.example.net')

    def test_foreign_host_via_icp_falls_back_to_default(self):
        self.ICP.set_param('connect.voicetel_api_host', 'attacker.example.com')
        self.assertEqual(self.Settings._voicetel_api_host(), 'voiceml.voicetel.com')

    def test_host_fields_are_system_only(self):
        for fname in ('voicetel_api_host', 'voicetel_sip_suffix', 'voicetel_stun_server',
                      'voicetel_media_hosts'):
            with self.subTest(field=fname):
                self.assertEqual(self.Settings._fields[fname].groups, 'base.group_system')
        user = new_test_user(self.env, login='vt_settings_plain')
        self.assertFalse(self.Settings.with_user(user).get_param('voicetel_api_host'))

    def test_missing_credentials_is_a_validation_error(self):
        self.Settings.sudo().set_param('voicetel_account_sid', False)
        with self.assertRaises(ValidationError):
            self.Settings.get_voicetel_client()


@tagged('post_install', '-at_install')
class TestTyped404(TransactionCase):

    def setUp(self):
        super().setUp()
        self.env['connect.settings'].set_param('voicetel_auto_sync', True)
        self.client = MagicMock()
        patch.object(type(self.env['connect.settings']), 'get_voicetel_client',
                     return_value=self.client).start()
        self.addCleanup(patch.stopall)
        self.domain = self.env['connect.voicetel.domain'].with_context(
            no_voicetel_create=True).create({
                'friendly_name': 'T', 'subdomain': 't404', 'sid': 'SD1',
                'cred_list_sid': 'CL1', 'delete_protection': False})

    def test_domain_gone_remotely_is_unlinked(self):
        self.client.sip.domains.delete.side_effect = NotFoundError('gone', status_code=404)
        self.domain.unlink()
        self.assertFalse(self.domain.exists())

    def test_other_error_text_mentioning_not_found_still_raises(self):
        self.client.sip.domains.delete.side_effect = ApiError(
            'credential list not found in region', status_code=500)
        with self.assertRaises(ValidationError):
            self.domain.unlink()
