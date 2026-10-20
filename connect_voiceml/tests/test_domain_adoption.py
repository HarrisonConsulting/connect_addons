# -*- coding: utf-8 -*-
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from odoo.tests import TransactionCase, tagged


class FakeApiError(Exception):
    def __init__(self, status_code=409, code=None):
        super().__init__('api error %s' % status_code)
        self.status_code = status_code
        self.code = code


def _client(remote_domains=(), reg_mappings=(), call_mappings=(), credentials=()):
    client = MagicMock()
    client.sip.domains.list.return_value = SimpleNamespace(domains=list(remote_domains))
    reg = client.sip.domains.auth.registrations.credential_list_mappings.return_value
    reg.list.return_value = SimpleNamespace(credential_list_mappings=list(reg_mappings))
    calls = client.sip.domains.auth.calls.credential_list_mappings.return_value
    calls.list.return_value = SimpleNamespace(credential_list_mappings=list(call_mappings))
    creds = client.sip.credential_lists.credentials.return_value
    creds.list.return_value = SimpleNamespace(credentials=list(credentials))
    creds.create.side_effect = lambda **kw: SimpleNamespace(sid='CRnew')
    client.sip.domains.create.side_effect = lambda **kw: SimpleNamespace(sid='DNnew')
    client.sip.credential_lists.create.side_effect = lambda **kw: SimpleNamespace(sid='CLnew')
    return client


@tagged('post_install', '-at_install')
class TestDomainAdoption(TransactionCase):

    def setUp(self):
        super().setUp()
        self.env['connect.settings'].set_param('api_url', 'https://odoo.example.com/')
        self.Domain = self.env['connect.voiceml.domain']
        self.voice_url = 'https://odoo.example.com/voiceml/webhook/domain'

    def _patch(self, client):
        return patch.object(type(self.env['connect.settings']), '_voiceml_client',
                            return_value=client)

    def _remote(self, name='yoder.example.com', voice_url='https://odoo.example.com/voicetel/webhook/domain'):
        return SimpleNamespace(sid='DNold', domain_name=name, friendly_name=name,
                               voice_url=voice_url, voice_method='POST')

    def test_sync_adopts_sid_cred_list_and_voice_url(self):
        client = _client(
            [self._remote()],
            reg_mappings=[SimpleNamespace(sid='CLold', friendly_name='x')],
            call_mappings=[SimpleNamespace(sid='CLold', friendly_name='x')])
        with self._patch(client):
            self.Domain.sync()
        rec = self.Domain.search([('domain_name', '=', 'yoder.example.com')])
        self.assertEqual(rec.sid, 'DNold')
        self.assertEqual(rec.cred_list_sid, 'CLold')
        client.sip.domains.update.assert_called_once_with(
            'DNold', voice_url=self.voice_url, voice_method='POST')
        client.sip.credential_lists.create.assert_not_called()
        client.sip.domains.create.assert_not_called()

    def test_sync_creates_list_and_mapping_when_absent(self):
        client = _client([self._remote()])
        with self._patch(client):
            self.Domain.sync()
        rec = self.Domain.search([('domain_name', '=', 'yoder.example.com')])
        self.assertEqual(rec.cred_list_sid, 'CLnew')
        client.sip.domains.auth.registrations.credential_list_mappings.return_value \
            .create.assert_called_once_with(credential_list_sid='CLnew')

    def test_second_create_makes_zero_posts(self):
        remote = self._remote(voice_url=self.voice_url)
        client = _client(
            [remote],
            reg_mappings=[SimpleNamespace(sid='CLold')],
            call_mappings=[SimpleNamespace(sid='CLold')])
        with self._patch(client):
            rec = self.Domain.create({'domain_name': 'yoder.example.com'})
            rec2 = self.Domain.create({'domain_name': 'yoder.example.com'})
        self.assertEqual((rec.sid, rec.cred_list_sid), ('DNold', 'CLold'))
        self.assertEqual((rec2.sid, rec2.cred_list_sid), ('DNold', 'CLold'))
        client.sip.domains.create.assert_not_called()
        client.sip.domains.update.assert_not_called()
        client.sip.credential_lists.create.assert_not_called()
        client.sip.domains.auth.registrations.credential_list_mappings.return_value \
            .create.assert_not_called()
        client.sip.domains.auth.calls.credential_list_mappings.return_value \
            .create.assert_not_called()

    def test_create_conflict_adopts_remote_sid(self):
        client = _client()
        remote = self._remote(voice_url=self.voice_url)
        client.sip.domains.list.side_effect = [
            SimpleNamespace(domains=[]), SimpleNamespace(domains=[remote])]
        client.sip.domains.create.side_effect = FakeApiError(409)
        with self._patch(client):
            rec = self.Domain.create({'domain_name': 'yoder.example.com'})
        self.assertEqual(rec.sid, 'DNold')
        self.assertTrue(rec.cred_list_sid)

    def test_create_new_domain_records_cred_list(self):
        client = _client()
        with self._patch(client):
            rec = self.Domain.create({'domain_name': 'fresh.example.com'})
        self.assertEqual((rec.sid, rec.cred_list_sid), ('DNnew', 'CLnew'))

    def test_unlink_swallows_404_only(self):
        client = _client()
        with self._patch(client):
            rec = self.Domain.create({'domain_name': 'gone.example.com'})
            client.sip.domains.delete.side_effect = FakeApiError(404)
            rec.unlink()
        with self._patch(client):
            rec = self.Domain.create({'domain_name': 'boom.example.com'})
            client.sip.domains.delete.side_effect = FakeApiError(500)
            with self.assertRaises(FakeApiError):
                rec.unlink()

    def test_user_provisioned_on_adopted_domain(self):
        client = _client(
            [self._remote(voice_url=self.voice_url)],
            reg_mappings=[SimpleNamespace(sid='CLold')],
            call_mappings=[SimpleNamespace(sid='CLold')])
        with self._patch(client):
            self.Domain.sync()
            domain = self.Domain.search([('domain_name', '=', 'yoder.example.com')])
            res_user = self.env['res.users'].create({'name': 'Desk', 'login': 'desk_adopt'})
            user = self.env['connect.user'].create({
                'user': res_user.id, 'name': 'Desk', 'voiceml_username': 'desk1',
                'voiceml_domain': domain.id, 'voiceml_sip_enabled': True,
                'voiceml_password': 'Passw0rdPassw0rd'})
        client.sip.credential_lists.credentials.assert_any_call('CLold')
        self.assertEqual(user.voiceml_sid, 'CRnew')

    def test_user_duplicate_username_adopts_sid_and_resets_password(self):
        client = _client(
            [self._remote(voice_url=self.voice_url)],
            reg_mappings=[SimpleNamespace(sid='CLold')],
            call_mappings=[SimpleNamespace(sid='CLold')],
            credentials=[SimpleNamespace(sid='CRother', username='x'),
                         SimpleNamespace(sid='CRdup', username='desk2')])
        creds = client.sip.credential_lists.credentials.return_value
        creds.create.side_effect = FakeApiError(409)
        with self._patch(client):
            self.Domain.sync()
            domain = self.Domain.search([('domain_name', '=', 'yoder.example.com')])
            res_user = self.env['res.users'].create({'name': 'Desk2', 'login': 'desk_dup'})
            user = self.env['connect.user'].create({
                'user': res_user.id, 'name': 'Desk2', 'voiceml_username': 'desk2',
                'voiceml_domain': domain.id, 'voiceml_sip_enabled': True,
                'voiceml_password': 'Passw0rdPassw0rd'})
        self.assertEqual(user.voiceml_sid, 'CRdup')
        creds.update.assert_called_once_with('CRdup', password='Passw0rdPassw0rd')
