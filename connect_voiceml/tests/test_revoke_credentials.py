# -*- coding: utf-8 -*-
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRevokeOrphanCredentials(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.domain = cls.env['connect.voiceml.domain'].with_context(install_mode=True).create({
            'domain_name': 'revoke.example.com'})
        cls.domain.cred_list_sid = 'CLrevoke'
        res_user = cls.env['res.users'].create({'name': 'Revoke Live', 'login': 'revoke_live'})
        cls.live_user = cls.env['connect.user'].create({
            'user': res_user.id, 'name': 'Revoke Live',
            'voiceml_username': 'liveagent', 'voiceml_domain': cls.domain.id,
            'voiceml_sip_enabled': True})

    def _client(self, usernames, delete_side_effect=None):
        creds = [SimpleNamespace(sid='CR_' + n, username=n) for n in usernames]
        client = MagicMock()
        handle = client.sip.credential_lists.credentials.return_value
        handle.list.return_value = SimpleNamespace(credentials=creds, next_page_uri=None)
        if delete_side_effect:
            handle.delete.side_effect = delete_side_effect
        return client, handle

    def _run(self, client, apply):
        with patch.object(type(self.env['connect.settings']), '_voiceml_client',
                          return_value=client):
            return self.env['connect.voiceml.domain'].search(
                [('id', '=', self.domain.id)])._revoke_orphan_credentials(apply=apply)

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
        self.live_user.active = True
        self.live_user.voiceml_sip_enabled = False
        ledger = self._run(client, apply=False)
        self.assertEqual([r['username'] for r in ledger], ['liveagent'])
        handle.delete.assert_not_called()

    def test_apply_marks_deleted_and_failed_and_continues(self):
        def delete(sid):
            if sid == 'CR_bad':
                raise RuntimeError('boom')
        client, handle = self._client(['liveagent', 'bad', 'ghost'], delete)
        ledger = self._run(client, apply=True)
        self.assertEqual({r['username']: r['action'] for r in ledger},
                         {'bad': 'failed', 'ghost': 'deleted'})
        self.assertIn('boom', next(r['error'] for r in ledger if r['username'] == 'bad'))
        self.assertEqual([c.args[0] for c in handle.delete.call_args_list],
                         ['CR_bad', 'CR_ghost'])
