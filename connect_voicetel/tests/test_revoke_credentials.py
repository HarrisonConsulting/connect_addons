# -*- coding: utf-8 -*-
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from voiceml.exceptions import NotFoundError

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from .test_migration import _drop_legacy_tables


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

    def setUp(self):
        super().setUp()
        # Legacy tables this database kept are not part of these cases.
        _drop_legacy_tables(self.env.cr)

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

    def test_legacy_browser_credentials_are_listed(self):
        """1.2.x per-tab credentials (connect.user_browser_credential,
        renamed by the migration) sit in the user's old core SIP domain."""
        cr = self.env.cr
        cr.execute("""
            DROP TABLE IF EXISTS connect_voicetel_legacy_domain;
            DROP TABLE IF EXISTS connect_voicetel_user_archive;
            CREATE TABLE IF NOT EXISTS connect_domain (
                id SERIAL PRIMARY KEY, subdomain varchar, cred_list_sid varchar);
            ALTER TABLE connect_user ADD COLUMN IF NOT EXISTS "domain" int;
            ALTER TABLE connect_user ADD COLUMN IF NOT EXISTS username varchar;
        """)
        # connect_twilio's FK on connect_user.domain points at its own table
        cr.execute("""
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'connect_user'::regclass AND contype = 'f'
            AND conkey = ARRAY[(SELECT attnum FROM pg_attribute
                WHERE attrelid = 'connect_user'::regclass AND attname = 'domain')]""")
        for (name,) in cr.fetchall():
            cr.execute('ALTER TABLE connect_user DROP CONSTRAINT "%s"' % name)
        cr.execute("""
            INSERT INTO connect_domain (subdomain, cred_list_sid) VALUES
                ('yoder', 'CLyoder'), ('current', 'CLrevoke') RETURNING id""")
        old_domain, current_domain = [row[0] for row in cr.fetchall()]
        cr.execute("""
            UPDATE connect_user SET "domain" = %s, username = 'matt' WHERE id = %s;
            CREATE TABLE connect_voicetel_legacy_user_browser_credential (
                id SERIAL PRIMARY KEY, "user" int, nonce varchar, sid varchar);
            INSERT INTO connect_voicetel_legacy_user_browser_credential ("user", nonce, sid)
                VALUES (%s, 'aaaa', 'CRtab1'), (%s, 'bbbb', 'CRtab2');
        """, (old_domain, self.live_user.id, self.live_user.id, self.live_user.id))
        other = self.env['connect.user'].with_context(no_voicetel_create=True).create({
            'user': self.env['res.users'].create({'name': 'Cur', 'login': 'revoke_cur'}).id})
        self.env.flush_all()
        cr.execute("""
            UPDATE connect_user SET "domain" = %s, username = 'cur' WHERE id = %s;
            INSERT INTO connect_voicetel_legacy_user_browser_credential ("user", nonce, sid)
                VALUES (%s, 'cccc', 'CRcurrentlist');
        """, (current_domain, other.id, other.id))
        client, handle = self._client(['liveagent'])
        ledger = self._run(client, apply=False)
        self.assertEqual(
            [(r['kind'], r['domain'], r['username'], r['sid'], r['action']) for r in ledger],
            [('legacy_credential', 'yoder', 'matt-web-aaaa', 'CRtab1', 'would_delete'),
             ('legacy_credential', 'yoder', 'matt-web-bbbb', 'CRtab2', 'would_delete')])
        handle.delete.assert_not_called()
        ledger = self._run(client, apply=True)
        client.sip.credential_lists.credentials.assert_any_call('CLyoder')
        self.assertEqual(sorted(c.args[0] for c in handle.delete.call_args_list),
                         ['CRtab1', 'CRtab2'])
        self.assertEqual({r['action'] for r in ledger}, {'deleted'})

    def test_non_admin_is_refused(self):
        user = new_test_user(self.env, login='vt_revoke_plain')
        client, handle = self._client(['ghost'])
        with patch.object(type(self.env['connect.settings']), 'get_voicetel_client',
                          return_value=client), self.assertRaises(AccessError):
            self.env['connect.voicetel.domain'].with_user(user)._revoke_orphan_credentials()
