# -*- coding: utf-8 -*-
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from voiceml.exceptions import NotFoundError

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestRevokeOrphanCredentials(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['connect.settings'].set_param('voicetel_auto_sync', False)
        cls.domain = cls.env['connect.voicetel.domain'].with_context(
            no_voicetel_create=True).create({
                'friendly_name': 'Revoke', 'subdomain': 'revoke',
                'sid': 'SDcurrent', 'cred_list_sid': 'CLrevoke'})
        res_user = cls.env['res.users'].create({'name': 'Revoke Live', 'login': 'revoke_live'})
        cls.live_user = cls.env['connect.user'].with_context(
            no_voicetel_create=True).create({
                'user': res_user.id, 'voicetel_username': 'liveagent',
                'voicetel_domain': cls.domain.id, 'voicetel_sip_enabled': True})

    def _client(self, usernames, delete_side_effect=None):
        creds = [SimpleNamespace(sid='CR_' + n, username=n) for n in usernames]
        client = MagicMock()
        handle = client.sip.credential_lists.credentials.return_value
        handle.iter.side_effect = lambda *a, **kw: iter(creds)
        if delete_side_effect:
            handle.delete.side_effect = delete_side_effect
        return client, handle

    def _run(self, client, apply):
        with patch.object(type(self.env['connect.settings']), 'get_voicetel_client',
                          return_value=client):
            return self.domain._revoke_orphan_credentials(apply=apply)

    def test_dry_run_lists_orphan_only_and_deletes_nothing(self):
        client, handle = self._client(['liveagent', 'ghost'])
        ledger = self._run(client, apply=False)
        self.assertEqual(
            [(r['username'], r['sid'], r['action']) for r in ledger],
            [('ghost', 'CR_ghost', 'would_delete')])
        handle.delete.assert_not_called()

    def test_archived_and_sip_disabled_users_are_orphans(self):
        self.live_user.active = False
        client, handle = self._client(['liveagent'])
        ledger = self._run(client, apply=False)
        self.assertEqual([r['username'] for r in ledger], ['liveagent'])
        handle.delete.assert_not_called()

    def test_apply_marks_deleted_failed_gone_and_continues(self):
        def delete(sid):
            if sid == 'CR_bad':
                raise RuntimeError('boom')
            if sid == 'CR_gone':
                raise NotFoundError('gone', status_code=404)
        client, handle = self._client(['liveagent', 'bad', 'gone', 'ghost'], delete)
        ledger = self._run(client, apply=True)
        self.assertEqual({r['username']: r['action'] for r in ledger},
                         {'bad': 'failed', 'gone': 'already_gone', 'ghost': 'deleted'})

    def test_legacy_archive_credentials_and_domains_are_listed(self):
        cr = self.env.cr
        cr.execute("""
            DROP TABLE IF EXISTS connect_voicetel_user_archive;
            CREATE TABLE connect_voicetel_legacy_domain (
                id int, sid varchar, cred_list_sid varchar, domain_name varchar);
            INSERT INTO connect_voicetel_legacy_domain VALUES
                (7, 'SDold', 'CLold', 'hc.sip.voicetel.com'),
                (8, 'SDcurrent', 'CLrevoke', 'same-as-current');
            CREATE TABLE connect_voicetel_user_archive (
                id int, voicetel_domain int, voicetel_username varchar, voicetel_sid varchar);
            INSERT INTO connect_voicetel_user_archive VALUES
                (1, 7, 'matt', 'CRold'), (2, 7, 'nosid', NULL);
        """)
        client, handle = self._client(['liveagent'])
        ledger = self._run(client, apply=False)
        self.assertEqual(
            sorted((r['kind'], r['sid']) for r in ledger),
            [('legacy_credential', 'CRold'), ('legacy_domain', 'SDold')])
        ledger = self._run(client, apply=True)
        client.sip.domains.delete.assert_called_once_with('SDold')
        client.sip.credential_lists.delete.assert_called_once_with('CLold')
        self.assertEqual({r['action'] for r in ledger}, {'deleted'})

    def test_non_admin_is_refused(self):
        user = new_test_user(self.env, login='vt_revoke_plain')
        client, handle = self._client(['ghost'])
        with patch.object(type(self.env['connect.settings']), 'get_voicetel_client',
                          return_value=client), self.assertRaises(AccessError):
            self.env['connect.voicetel.domain'].with_user(user)._revoke_orphan_credentials()
