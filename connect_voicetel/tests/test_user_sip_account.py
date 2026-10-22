# -*- coding: utf-8 -*-
"""SIP credential lifecycle and the browser phone's configuration channel.

The credential is created in the domain's credential list when the SIP
phone is enabled; the password stays readable by its owner because the
browser registers with it (ADR-066 decisions 4 and 5).
"""
from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.tests import tagged, new_test_user

from .common import VoiceTelTestCommon


@tagged('at_install', '-post_install')
class TestUserSipAccount(VoiceTelTestCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        settings = cls.env['connect.settings'].sudo()
        settings.set_param('voicetel_auto_sync', True)
        Settings = type(cls.env['connect.settings'])
        with patch.object(Settings, 'get_voicetel_client', return_value=None):
            cls.domain = cls.env['connect.voicetel.domain'].with_context(
                no_voicetel_create=True).create({
                    'friendly_name': 'sip-test',
                    'subdomain': 'siptest',
                    'sid': 'SD' + '0' * 32,
                    'cred_list_sid': 'CL' + '0' * 32,
                })

    def _user(self, login, **kwargs):
        odoo_user = new_test_user(
            self.env, login=login, groups='base.group_user,connect.group_user')
        User = type(self.env['connect.user'])
        with patch.object(User, '_voicetel_create_sip_account',
                          return_value='CR' + '1' * 32) as create:
            vals = {
                'user': odoo_user.id,
                'voicetel_sip_enabled': True,
                'voicetel_username': login,
                'voicetel_domain': self.domain.id,
            }
            vals.update(kwargs)
            pbx_user = self.env['connect.user'].with_context(
                no_clear_cache=True).create(vals)
        return odoo_user, pbx_user, create

    def test_enabling_sip_creates_the_credential_and_a_password(self):
        odoo_user, pbx_user, create = self._user('vtsip1')

        create.assert_called_once()
        self.assertEqual(pbx_user.voicetel_sid, 'CR' + '1' * 32)
        self.assertTrue(pbx_user.voicetel_password)
        self.assertGreaterEqual(len(pbx_user.voicetel_password), 12)
        self.assertNotIn('*', pbx_user.voicetel_password)

    def test_the_browser_phone_gets_its_own_credential_only(self):
        odoo_user, pbx_user, _ = self._user('vtsip2', voicetel_phone_enabled=True)

        config = self.env['res.users'].with_user(odoo_user).get_voicetel_sip_user_config()

        self.assertEqual(config['user_config']['sip_user'], 'vtsip2')
        self.assertEqual(
            config['user_config']['sip_password'], pbx_user.voicetel_password)
        self.assertEqual(
            config['user_config']['phone_sip_proxy'], 'siptest.vml.voice.tel')
        self.assertEqual(
            config['user_config']['phone_websocket'],
            'wss://siptest.vml.voice.tel:8443')
        self.assertIn('call_popup_is_enabled', config['phone_config'])

    def test_another_user_cannot_read_the_credential(self):
        odoo_user, pbx_user, _ = self._user('vtsip3', voicetel_phone_enabled=True)
        other = new_test_user(
            self.env, login='vtsip3other',
            groups='base.group_user,connect.group_user')

        self.assertFalse(
            self.env['res.users'].with_user(other).get_voicetel_sip_user_config())

    def test_browser_phone_needs_the_sip_account(self):
        odoo_user, pbx_user, _ = self._user('vtsip4', voicetel_phone_enabled=False)

        self.assertFalse(
            self.env['res.users'].with_user(odoo_user).get_voicetel_sip_user_config())

    def test_disabling_sip_drops_the_credential(self):
        odoo_user, pbx_user, _ = self._user('vtsip5')
        User = type(self.env['connect.user'])
        with patch.object(User, 'voicetel_delete_sip_account',
                          return_value=True) as delete:
            pbx_user.voicetel_sip_enabled = False

        delete.assert_called_once()
        self.assertFalse(pbx_user.voicetel_sid)
        self.assertFalse(self.env['connect.voicetel.user_callflow'].search(
            [('user', '=', pbx_user.id), ('callflow_type', '=', 'sip')]))

    def test_sip_phone_requires_username_and_domain(self):
        from odoo.exceptions import ValidationError
        odoo_user = new_test_user(self.env, login='vtsip6')
        with self.assertRaises(ValidationError):
            self.env['connect.user'].with_context(
                no_clear_cache=True, no_voicetel_create=True).create({
                    'user': odoo_user.id,
                    'voicetel_sip_enabled': True,
                    'voicetel_domain': False,
                })

    def test_owner_cannot_read_the_password_field_directly(self):
        odoo_user, pbx_user, _ = self._user('vtsip7', voicetel_phone_enabled=True)
        with self.assertRaises(AccessError):
            pbx_user.with_user(odoo_user).read(['voicetel_password'])

    def test_deleting_a_domain_keeps_its_users(self):
        _odoo_user, pbx_user, _ = self._user('vtsip8')
        domain = self.env['connect.voicetel.domain'].with_context(
            no_voicetel_create=True).create({
                'friendly_name': 'gone', 'subdomain': 'gonedomain',
                'delete_protection': False})
        pbx_user.with_context(skip_voicetel_sync=True).write({
            'voicetel_sip_enabled': False, 'voicetel_domain': domain.id})
        self.env['connect.settings'].set_param('voicetel_auto_sync', False)
        domain.unlink()
        self.assertTrue(pbx_user.exists())
        self.assertFalse(pbx_user.voicetel_domain)

    def test_no_domain_without_the_phone(self):
        odoo_user = new_test_user(self.env, login='vtsip9')
        pbx_user = self.env['connect.user'].with_context(
            no_clear_cache=True, no_voicetel_create=True).create({'user': odoo_user.id})
        self.assertFalse(pbx_user.voicetel_domain)

    def test_conflict_adopts_and_resets_the_password(self):
        from unittest.mock import MagicMock
        from types import SimpleNamespace
        from voiceml.exceptions import ApiError
        _odoo_user, pbx_user, _ = self._user('vtsip10')
        client = MagicMock()
        creds = client.sip.credential_lists.credentials.return_value
        creds.create.side_effect = ApiError('taken', status_code=409, code=21632)
        creds.iter.return_value = iter([SimpleNamespace(username='vtsip10', sid='CRtaken')])
        sid = pbx_user._voicetel_create_sip_account('vtsip10', 'Newpass123', client=client)
        self.assertEqual(sid, 'CRtaken')
        creds.update.assert_called_once_with('CRtaken', password='Newpass123')

    def test_user_delete_survives_an_api_failure(self):
        from voiceml.exceptions import ApiError
        _odoo_user, pbx_user, _ = self._user('vtsip11')
        User = type(self.env['connect.user'])

        def boom(rec):
            raise ApiError('boom', status_code=500)
        # A plain function, not a MagicMock: unlink() collects methods
        # carrying an _ondelete attribute, which a mock fakes.
        with patch.object(User, 'voicetel_delete_sip_account', boom):
            pbx_user.unlink()
        self.assertFalse(pbx_user.exists())

    def test_passwords_are_strong_and_random(self):
        User = self.env['connect.user']
        passwords = {User.generate_voicetel_password() for _i in range(20)}
        self.assertEqual(len(passwords), 20)
        for password in passwords:
            self.assertEqual(len(password), 20)
