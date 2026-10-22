# -*- coding: utf-8 -*-
"""19.0.4.0.0 migration: shim, pre-retirement module and connect_voiceml."""
import importlib.util
import os

from odoo.tests import TransactionCase, tagged

from odoo.addons.connect_voicetel import hooks

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_script(name):
    path = os.path.join(_HERE, 'migrations', '19.0.4.0.0', name)
    spec = importlib.util.spec_from_file_location('vt_' + name.replace('-', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@tagged('post_install', '-at_install')
class TestMigration(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pre = _load_script('pre-migration.py')
        cls.post = _load_script('post-migration.py')

    def setUp(self):
        super().setUp()
        self.env.flush_all()
        cr = self.env.cr
        # A model table and an m2m table the retired module left behind,
        # plus a core-side table still pointing at it.
        cr.execute("""
            CREATE TABLE connect_voicetel_browser_credential (
                id SERIAL PRIMARY KEY, secret varchar,
                CONSTRAINT connect_voicetel_browser_credential_secret_uniq UNIQUE (secret));
            INSERT INTO connect_voicetel_browser_credential (secret) VALUES ('s1'), ('s2');
            CREATE TABLE connect_user_connect_voicetel_browser_credential_rel (
                cred_id int REFERENCES connect_voicetel_browser_credential(id));
            CREATE TABLE vt_test_core_ref (
                cred int CONSTRAINT vt_test_core_ref_cred_fkey
                REFERENCES connect_voicetel_browser_credential(id));
            DROP TABLE IF EXISTS connect_voicetel_settings_archive;
            DROP TABLE IF EXISTS connect_voicetel_user_archive;
            CREATE TABLE connect_voicetel_user_archive (id int, voicetel_password varchar);
            INSERT INTO connect_voicetel_user_archive VALUES (1, 'old');
            DELETE FROM ir_config_parameter WHERE key IN (
                'connect.voicetel_api_host', 'connect.voicetel_rest_host',
                'connect.voicetel_wss_url');
            INSERT INTO ir_config_parameter (key, value)
                VALUES ('connect.voicetel_rest_host', 'https://VoiceML.voicetel.com:443/2010'),
                       ('connect.voicetel_wss_url', 'wss://old'),
                       ('connect.voicetel_auto_sync', 'True');
            DELETE FROM ir_module_module WHERE name = 'connect_voiceml';
            DELETE FROM ir_model_data WHERE module = 'base' AND name = 'module_connect_voiceml';
        """)
        cr.execute("""
            INSERT INTO ir_module_module (name, state, latest_version)
            VALUES ('connect_voiceml', 'uninstalled', '19.0.1.2.1') RETURNING id""")
        self.voiceml_id = cr.fetchone()[0]
        cr.execute("""
            INSERT INTO ir_module_module_dependency (module_id, name) VALUES (%s, 'connect');
            INSERT INTO ir_model_data (module, name, model, res_id, noupdate)
            VALUES ('base', 'module_connect_voiceml', 'ir.module.module', %s, true);
            INSERT INTO ir_config_parameter (key, value)
            VALUES ('connect.voiceml_api_key', 'k')
        """, (self.voiceml_id, self.voiceml_id))

    def _regclass(self, name):
        self.env.cr.execute('SELECT to_regclass(%s)', ('"%s"' % name,))
        return self.env.cr.fetchone()[0]

    def _icp(self, key):
        self.env.cr.execute('SELECT value FROM ir_config_parameter WHERE key = %s', (key,))
        row = self.env.cr.fetchone()
        return row and row[0]

    def test_shim_upgrade_is_idempotent(self):
        for _run in range(2):
            self.pre.migrate(self.env.cr, '19.0.3.0.1')
            self.post.migrate(self.env.cr, '19.0.3.0.1')
        # legacy tables renamed, never dropped
        self.assertIsNone(self._regclass('connect_voicetel_browser_credential'))
        self.assertTrue(self._regclass('connect_voicetel_legacy_browser_credential'))
        self.env.cr.execute('SELECT count(*) FROM connect_voicetel_legacy_browser_credential')
        self.assertEqual(self.env.cr.fetchone()[0], 2)
        self.assertIsNone(self._regclass('connect_user_connect_voicetel_browser_credential_rel'))
        self.env.cr.execute(
            "SELECT count(*) FROM pg_class WHERE relname LIKE 'connect\\_voicetel\\_legacy\\_connect\\_user%%'")
        self.assertEqual(self.env.cr.fetchone()[0], 1)
        # the unique index name is free again for a new table of that name
        self.assertIsNone(self._regclass('connect_voicetel_browser_credential_secret_uniq'))
        # a core-side FK to the legacy table is gone
        self.env.cr.execute(
            "SELECT 1 FROM pg_constraint WHERE conname = 'vt_test_core_ref_cred_fkey'")
        self.assertFalse(self.env.cr.fetchone())
        # this version's own tables are untouched
        self.assertTrue(self._regclass('connect_voicetel_number'))
        self.assertTrue(self._regclass('connect_voicetel_domain'))
        self.env['connect.voicetel.number'].search([])
        # archives kept
        self.assertTrue(self._regclass('connect_voicetel_user_archive'))
        # connect_voiceml row, its dependency, base xmlid and ICPs removed
        self.env.cr.execute("SELECT 1 FROM ir_module_module WHERE name = 'connect_voiceml'")
        self.assertFalse(self.env.cr.fetchone())
        self.env.cr.execute(
            "SELECT 1 FROM ir_module_module_dependency WHERE module_id = %s", (self.voiceml_id,))
        self.assertFalse(self.env.cr.fetchone())
        self.env.cr.execute(
            "SELECT 1 FROM ir_model_data WHERE module = 'base' AND name = 'module_connect_voiceml'")
        self.assertFalse(self.env.cr.fetchone())
        self.assertFalse(self._icp('connect.voiceml_api_key'))
        # settings carried and forced
        self.assertEqual(self._icp('connect.voicetel_api_host'), 'voiceml.voicetel.com')
        self.assertFalse(self._icp('connect.voicetel_rest_host'))
        self.assertFalse(self._icp('connect.voicetel_wss_url'))
        self.assertEqual(self._icp('connect.voicetel_auto_sync'), 'False')
        self.assertEqual(self._icp('connect.voicetel_verify_requests'), 'True')

    def test_foreign_rest_host_is_not_carried(self):
        self.env.cr.execute(
            "UPDATE ir_config_parameter SET value = 'evil.example.com'"
            " WHERE key = 'connect.voicetel_rest_host'")
        self.pre.migrate(self.env.cr, '19.0.3.0.1')
        self.assertFalse(self._icp('connect.voicetel_api_host'))
        self.assertFalse(self._icp('connect.voicetel_rest_host'))

    def test_installed_voiceml_refuses(self):
        self.env.cr.execute(
            "UPDATE ir_module_module SET state = 'installed' WHERE id = %s", (self.voiceml_id,))
        with self.assertRaisesRegex(Exception, 'uninstall connect_voiceml first'):
            self.pre.migrate(self.env.cr, '19.0.3.0.1')

    def test_fresh_install_is_a_no_op(self):
        self.pre.migrate(self.env.cr, False)
        self.assertTrue(self._regclass('connect_voicetel_browser_credential'))

    def test_pre_init_hook_renames_leftover_tables(self):
        cr = self.env.cr
        # A table whose model ir.model still records under this module
        # is not a leftover.
        cr.execute("""
            CREATE TABLE connect_voicetel_kept (id SERIAL PRIMARY KEY);
            INSERT INTO ir_model (model, name, state, "order", transient)
            VALUES ('connect.voicetel.kept', '{"en_US": "Kept"}', 'base', 'id', false)
            RETURNING id""")
        model_id = cr.fetchone()[0]
        cr.execute("""
            INSERT INTO ir_model_data (module, name, model, res_id, noupdate)
            VALUES ('connect_voicetel', 'model_connect_voicetel_kept', 'ir.model', %s, false)
        """, (model_id,))
        for _run in range(2):
            hooks.pre_init_hook(self.env)
        self.assertIsNone(self._regclass('connect_voicetel_browser_credential'))
        self.assertTrue(self._regclass('connect_voicetel_legacy_browser_credential'))
        cr.execute('SELECT count(*) FROM connect_voicetel_legacy_browser_credential')
        self.assertEqual(cr.fetchone()[0], 2)
        # its unique index name is free for the table this module creates
        self.assertIsNone(self._regclass('connect_voicetel_browser_credential_secret_uniq'))
        cr.execute("SELECT 1 FROM pg_constraint WHERE conname = 'vt_test_core_ref_cred_fkey'")
        self.assertFalse(cr.fetchone())
        self.assertTrue(self._regclass('connect_voicetel_kept'))
        # this version's tables, archives and the voiceml row are not touched
        self.assertTrue(self._regclass('connect_voicetel_number'))
        self.assertTrue(self._regclass('connect_voicetel_user_archive'))
        self.assertIsNone(self._regclass('connect_voicetel_legacy_number'))
        cr.execute("SELECT 1 FROM ir_module_module WHERE name = 'connect_voiceml'")
        self.assertTrue(cr.fetchone())

    def test_post_init_hook_removes_voiceml_row(self):
        hooks.post_init_hook(self.env)
        self.env.cr.execute("SELECT 1 FROM ir_module_module WHERE name = 'connect_voiceml'")
        self.assertFalse(self.env.cr.fetchone())

    def test_pre_retirement_path_archives_then_drops(self):
        cr = self.env.cr
        cr.execute("""
            DROP TABLE connect_voicetel_user_archive;
            ALTER TABLE connect_user ADD COLUMN voicetel_client_enabled boolean;
            UPDATE connect_user SET voicetel_client_enabled = true,
                voicetel_password = 'legacy-pw', originate_provider = NULL;
            SELECT id FROM connect_user LIMIT 1;
        """)
        row = cr.fetchone()
        if not row:
            self.skipTest('no connect.user row to archive')
        user_id = row[0]
        self.pre.migrate(cr, '19.0.2.1.3')
        cr.execute(
            'SELECT voicetel_password, voicetel_client_enabled'
            ' FROM connect_voicetel_user_archive WHERE id = %s', (user_id,))
        self.assertEqual(cr.fetchone(), ('legacy-pw', True))
        cr.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'connect_user'"
            " AND column_name IN ('voicetel_password', 'voicetel_client_enabled')")
        self.assertFalse(cr.fetchall())
        self.assertIsNone(self._regclass('connect_voicetel_browser_credential'))
        # second run: the archive exists, nothing more is copied or dropped
        self.pre.migrate(cr, '19.0.2.1.3')
        cr.execute('SELECT count(*) FROM connect_voicetel_user_archive')
        self.assertTrue(cr.fetchone()[0])
