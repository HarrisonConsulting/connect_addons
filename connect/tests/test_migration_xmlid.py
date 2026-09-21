import importlib.util
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from odoo.tests import TransactionCase, tagged
from odoo.addons.base.models.ir_model import selection_xmlid


def _migration_module(version='19.0.4.4.0', filename='pre-migration.py'):
    path = Path(__file__).parents[1] / 'migrations' / version / filename
    spec = importlib.util.spec_from_file_location(
        'connect_migration_%s_%s' % (version.replace('.', '_'), filename.replace('-', '_')),
        path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _SelectionLoopCursor:
    """Run migration SQL while bypassing only the unrelated model-rename guard."""

    def __init__(self, cursor):
        self._cursor = cursor
        self._model_xmlid_check = False

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def execute(self, query, params=None):
        self._model_xmlid_check = 'SELECT 1 FROM ir_model_data' in query
        if not self._model_xmlid_check:
            return self._cursor.execute(query, params)

    def fetchone(self):
        if self._model_xmlid_check:
            return None
        return self._cursor.fetchone()


@tagged('post_install', '-at_install')
class TestMigrationXmlid(TransactionCase):
    def test_sms_composer_move_preserves_record_and_source_path(self):
        """A moved composer keeps its identity and reads its current source file."""
        migration = _migration_module('19.0.4.4.3')
        suffix = uuid4().hex
        legacy_name = 'legacy_composer_%s' % suffix
        canonical_name = 'canonical_composer_%s' % suffix
        view = self.env['ir.ui.view'].create({
            'name': legacy_name, 'model': 'res.partner',
            'type': 'form', 'arch': '<form/>',
        })
        view.write({'arch_fs': 'connect/wizard/sms_composer_views.xml'})
        xmlid = self.env['ir.model.data'].create({
            'module': 'connect', 'name': legacy_name,
            'model': 'ir.ui.view', 'res_id': view.id,
        })
        migration._move_sms_composer_view(self.env.cr, legacy_name, canonical_name)
        migration._move_sms_composer_view(self.env.cr, legacy_name, canonical_name)
        xmlid.invalidate_recordset()
        view.invalidate_recordset()
        self.assertEqual(xmlid.module, 'connect_twilio')
        self.assertEqual(xmlid.name, canonical_name)
        self.assertEqual(xmlid.res_id, view.id)
        self.assertEqual(view.arch_fs, 'connect_twilio/wizard/sms_composer_views.xml')

    def test_selection_xmlid_normalization_matches_core_contract(self):
        """Migration SQL writes the canonical XMLID for a space-containing value."""
        migration = _migration_module()
        field = self.env['ir.model.fields'].search([
            ('model', '=', 'ir.ui.view'), ('name', '=', 'type'),
        ], limit=1)
        self.assertTrue(field)
        value = 'Mi.Gration Value %s' % uuid4().hex
        selection = self.env['ir.model.fields.selection'].create({
            'field_id': field.id, 'value': value, 'name': value,
        })
        xmlid = self.env['ir.model.data'].create({
            'module': 'connect_migration_fixture',
            'name': 'fixture_%s' % uuid4().hex,
            'model': 'ir.model.fields.selection', 'res_id': selection.id,
            'noupdate': True,
        })
        migration._rename_auto_xmlids(
            _SelectionLoopCursor(self.env.cr), 'ir.ui.view.legacy', 'ir.ui.view')
        xmlid.invalidate_recordset(['name'])
        self.assertEqual(
            xmlid.name,
            selection_xmlid('connect_migration_fixture', 'ir.ui.view', 'type', value).split('.', 1)[1],
        )

    def test_xmlid_reconciliation_preserves_legacy_records_and_references(self):
        """Canonical XMLIDs preserve existing records before current data reloads."""
        migration = _migration_module('19.0.4.4.3')
        suffix = uuid4().hex
        legacy_view = self.env['ir.ui.view'].create({
            'name': 'legacy-%s' % suffix,
            'model': 'res.partner',
            'type': 'form',
            'arch': '<form/>',
        })
        duplicate_view = self.env['ir.ui.view'].create({
            'name': 'duplicate-%s' % suffix,
            'model': 'res.partner',
            'type': 'form',
            'arch': '<form/>',
        })
        inherited_view = self.env['ir.ui.view'].create({
            'name': 'inherited-%s' % suffix,
            'model': 'res.partner',
            'type': 'form',
            'inherit_id': duplicate_view.id,
            'arch': '<data/>',
        })
        legacy_search = self.env['ir.ui.view'].create({
            'name': 'legacy-search-' + suffix, 'model': 'res.partner',
            'type': 'search', 'arch': '<search/>',
        })
        duplicate_search = self.env['ir.ui.view'].create({
            'name': 'duplicate-search-' + suffix, 'model': 'res.partner',
            'type': 'search', 'arch': '<search/>',
        })
        action = self.env['ir.actions.act_window'].create({
            'name': 'reconciliation-' + suffix, 'res_model': 'res.partner',
            'view_mode': 'form', 'view_id': duplicate_view.id,
            'search_view_id': duplicate_search.id,
        })
        binding = self.env['ir.actions.act_window.view'].create({
            'act_window_id': action.id, 'view_mode': 'form',
            'view_id': duplicate_view.id,
        })
        legacy_name = 'legacy_%s' % suffix
        canonical_name = 'canonical_%s' % suffix
        self.env['ir.model.data'].create({
            'module': 'connect', 'name': legacy_name,
            'model': 'ir.ui.view', 'res_id': legacy_view.id,
        })
        canonical_xmlid = self.env['ir.model.data'].create({
            'module': 'connect', 'name': canonical_name,
            'model': 'ir.ui.view', 'res_id': duplicate_view.id,
        })
        pair = migration._reconcile_xmlid(
            self.env.cr, 'ir.ui.view', legacy_name, canonical_name)
        self.assertEqual(pair, (legacy_view.id, duplicate_view.id))
        migration._reconcile_view_references(
            self.env.cr, [pair, (legacy_search.id, duplicate_search.id)])
        canonical_xmlid.invalidate_recordset(['res_id'])
        inherited_view.invalidate_recordset(['inherit_id'])
        self.assertEqual(canonical_xmlid.res_id, legacy_view.id)
        self.assertEqual(inherited_view.inherit_id, legacy_view)

        action.invalidate_recordset(['view_id', 'search_view_id'])
        binding.invalidate_recordset(['view_id'])
        self.assertEqual(action.view_id, legacy_view)
        self.assertEqual(action.search_view_id, legacy_search)
        self.assertEqual(binding.view_id, legacy_view)

    def test_unsupported_identity_collision_refuses_before_repointing(self):
        """Group/category semantics cannot be silently merged by XMLID alone."""
        migration = _migration_module('19.0.4.4.3')
        suffix = uuid4().hex
        categories = self.env['ir.module.category'].create([
            {'name': 'legacy-' + suffix}, {'name': 'canonical-' + suffix},
        ])
        names = ['legacy_' + suffix, 'canonical_' + suffix]
        xmlids = self.env['ir.model.data'].create([
            {'module': 'connect', 'name': name, 'model': 'ir.module.category',
             'res_id': category.id}
            for name, category in zip(names, categories)
        ])
        with self.assertRaisesRegex(ValueError, 'without merging their references'):
            migration._reconcile_xmlid(self.env.cr, 'ir.module.category', *names)
        xmlids.invalidate_recordset(['res_id'])
        self.assertEqual(xmlids.mapped('res_id'), categories.ids)


    def test_optional_pbx_table_is_allowed_but_declared_missing_table_fails(self):
        """PBX models may install after Connect, but declared tables cannot vanish."""
        migration = _migration_module('19.0.3.1.0')
        optional = ('connect.migration_optional_%s' % uuid4().hex, 'optional_table_%s' % uuid4().hex)
        declared = ('connect.documentation', 'missing_table_%s' % uuid4().hex)
        self.assertEqual(migration._declared_but_missing_tables(self.env.cr, (optional,)), [])
        self.assertEqual(
            migration._declared_but_missing_tables(self.env.cr, (declared,)),
            [declared],
        )

    def test_pbx_count_guard_allows_optional_absence_and_preserves_zero(self):
        """A missing pre-snapshot stays optional while a present zero-row table is checked."""
        migration = _migration_module('19.0.4.4.0', 'post-migration.py')
        tables = ('optional_table', 'present_empty_table')
        snapshot = {'optional_table': None, 'present_empty_table': 0}
        with patch.object(migration, '_count', side_effect=(None, 0)):
            migration._assert_pbx_table_counts(None, snapshot, tables)
        with patch.object(migration, '_count', side_effect=(None, 1)):
            with self.assertRaises(AssertionError):
                migration._assert_pbx_table_counts(None, snapshot, tables)


    def test_legacy_alias_is_removed_after_canonical_identity_is_preserved(self):
        """Only the canonical XMLID remains when both names own the same view."""
        migration = _migration_module('19.0.4.4.3')
        suffix = uuid4().hex
        view = self.env['ir.ui.view'].create({
            'name': 'alias-%s' % suffix, 'model': 'res.partner',
            'type': 'form', 'arch': '<form/>',
        })
        legacy_name = 'legacy_alias_%s' % suffix
        canonical_name = 'canonical_alias_%s' % suffix
        self.env['ir.model.data'].create([
            {'module': 'connect', 'name': legacy_name,
             'model': 'ir.ui.view', 'res_id': view.id},
            {'module': 'connect', 'name': canonical_name,
             'model': 'ir.ui.view', 'res_id': view.id},
        ])
        migration._remove_legacy_aliases(
            self.env.cr, (('ir.ui.view', legacy_name, canonical_name),))
        self.assertFalse(self.env['ir.model.data'].search([
            ('module', '=', 'connect'), ('name', '=', legacy_name),
        ]))
        self.assertEqual(self.env['ir.model.data'].search([
            ('module', '=', 'connect'), ('name', '=', canonical_name),
        ]).res_id, view.id)

    def test_legacy_only_xmlid_is_promoted_to_canonical_identity(self):
        """An earlier migration's legacy-only XMLID becomes its canonical XMLID."""
        migration = _migration_module('19.0.4.4.3')
        suffix = uuid4().hex
        view = self.env['ir.ui.view'].create({
            'name': 'legacy-only-%s' % suffix, 'model': 'res.partner',
            'type': 'form', 'arch': '<form/>',
        })
        legacy_name = 'legacy_only_%s' % suffix
        canonical_name = 'canonical_only_%s' % suffix
        self.env['ir.model.data'].create({
            'module': 'connect', 'name': legacy_name,
            'model': 'ir.ui.view', 'res_id': view.id,
        })
        self.assertFalse(migration._reconcile_xmlid(
            self.env.cr, 'ir.ui.view', legacy_name, canonical_name))
        self.assertFalse(self.env['ir.model.data'].search([
            ('module', '=', 'connect'), ('name', '=', legacy_name),
        ]))
        canonical = self.env['ir.model.data'].search([
            ('module', '=', 'connect'), ('name', '=', canonical_name),
        ])
        self.assertEqual(canonical.res_id, view.id)

    def test_moved_model_references_preserve_xmlid_identity(self):
        """Provider model renames update only exact model-string references."""
        migration = _migration_module('19.0.4.4.0')
        suffix = uuid4().hex
        target = self.env.user.partner_id
        moved = self.env['ir.model.data'].create({
            'module': 'connect_twilio', 'name': 'moved_%s' % suffix,
            'model': 'connect.twiml', 'res_id': target.id,
        })
        untouched = self.env['ir.model.data'].create({
            'module': 'connect_twilio', 'name': 'untouched_%s' % suffix,
            'model': 'connect.unrelated', 'res_id': target.id,
        })
        migration._rename_model_references(
            self.env.cr, 'connect.twiml', 'connect.twilio.twiml')
        moved.invalidate_recordset(['model', 'res_id', 'module'])
        untouched.invalidate_recordset(['model'])
        self.assertEqual(moved.model, 'connect.twilio.twiml')
        self.assertEqual(moved.res_id, target.id)
        self.assertEqual(moved.module, 'connect_twilio')
        self.assertEqual(untouched.model, 'connect.unrelated')


    def test_removed_portal_view_is_retired_only_after_reference_checks(self):
        """A removed portal template loses its XMLID and view without leaving a file path."""
        migration = _migration_module('19.0.4.4.3')
        suffix = uuid4().hex
        name = 'removed_portal_%s' % suffix
        view = self.env['ir.ui.view'].create({
            'name': name, 'model': 'res.partner', 'type': 'form', 'arch': '<form/>',
        })
        self.env['ir.model.data'].create({
            'module': 'connect', 'name': name, 'model': 'ir.ui.view', 'res_id': view.id,
        })
        migration._retire_removed_portal_views(self.env.cr, (name,))
        self.assertFalse(self.env['ir.model.data'].search([
            ('module', '=', 'connect'), ('name', '=', name),
        ]))
        self.assertFalse(self.env['ir.ui.view'].browse(view.id).exists())


    def test_dangling_legacy_alias_refuses_migration(self):
        """A legacy XMLID cannot survive without its canonical replacement."""
        migration = _migration_module('19.0.4.4.3')
        suffix = uuid4().hex
        view = self.env['ir.ui.view'].create({
            'name': 'dangling-%s' % suffix, 'model': 'res.partner',
            'type': 'form', 'arch': '<form/>',
        })
        legacy_name = 'legacy_dangling_%s' % suffix
        canonical_name = 'canonical_dangling_%s' % suffix
        self.env['ir.model.data'].create({
            'module': 'connect', 'name': legacy_name,
            'model': 'ir.ui.view', 'res_id': view.id,
        })
        with self.assertRaisesRegex(AssertionError, 'without canonical'):
            migration._remove_legacy_aliases(
                self.env.cr, (('ir.ui.view', legacy_name, canonical_name),))
