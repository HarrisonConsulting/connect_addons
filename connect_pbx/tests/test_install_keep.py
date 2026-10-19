import importlib.util
import os

from odoo.modules.module import get_module_path
from odoo.tests import TransactionCase, tagged
from odoo.tools.sql import table_exists

from odoo.addons.connect_pbx import INSTALL_KEEP_TABLE, restore_install_keep


def _connect_migration(version, filename):
    path = os.path.join(get_module_path('connect'), 'migrations', version, filename)
    spec = importlib.util.spec_from_file_location(
        'connect_migration_%s' % version.replace('.', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@tagged('post_install', '-at_install')
class TestInstallKeep(TransactionCase):
    """connect_pbx installing over monolithic connect data keeps it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.migration = _connect_migration('19.0.4.5.2', 'pre-migration.py')

    def _imd(self, name):
        self.env.cr.execute(
            "SELECT module, noupdate FROM ir_model_data WHERE name = %s"
            " AND module IN ('connect', 'connect_pbx')", (name,))
        return self.env.cr.fetchall()

    def test_declared_xmlids_are_read_from_the_data_files(self):
        declared = self.migration.declared_xmlids('connect_pbx')
        self.assertIs(declared['schedule_us_business_hours'], True)
        self.assertIs(declared['schedule_line_monday'], True)
        self.assertIs(declared['ir_cron_park_timeout'], True)
        self.assertIs(declared['connect_audio_form'], False)
        self.assertFalse(
            set(declared) & set(self.migration.declared_xmlids('connect')))

    def test_connect_owned_schedule_is_reowned_not_duplicated(self):
        cr = self.env.cr
        cr.execute(
            "UPDATE ir_model_data SET module = 'connect'"
            " WHERE module = 'connect_pbx' AND name = 'schedule_us_business_hours'")
        moved = self.migration.reown(cr, ['schedule_us_business_hours'])
        self.assertEqual(moved, 1)
        self.assertEqual(self._imd('schedule_us_business_hours'), [('connect_pbx', True)])

    def test_reown_leaves_a_clash_alone(self):
        cr = self.env.cr
        schedule = self.env.ref('connect_pbx.schedule_us_business_hours')
        self.env['ir.model.data'].create({
            'module': 'connect', 'name': 'schedule_us_business_hours',
            'model': 'connect.schedule', 'res_id': schedule.id, 'noupdate': True,
        })
        self.assertEqual(self.migration.reown(cr, ['schedule_us_business_hours']), 0)
        self.assertEqual(
            sorted(self._imd('schedule_us_business_hours')),
            [('connect', True), ('connect_pbx', True)])

    def test_snapshot_survives_an_install_mode_reload(self):
        cr = self.env.cr
        schedule = self.env.ref('connect_pbx.schedule_us_business_hours')
        monday = self.env.ref('connect_pbx.schedule_line_monday')
        stage = self.env.ref('connect_pbx.voicemail_stage_pending')
        schedule.write({'name': 'Yoder Hours', 'timezone': 'US/Central'})
        monday.write({'start_hour': 7.5, 'end_hour': 18.0})
        stage.write({'name': 'Inbox'})
        self.env.flush_all()

        kept = self.migration.snapshot(
            cr, ['schedule_us_business_hours', 'schedule_line_monday',
                 'voicemail_stage_pending'])
        self.assertEqual(kept, 3)

        # What connect_pbx's init-mode data load does to existing records.
        schedule.write({'name': 'US Business Hours', 'timezone': 'US/Eastern'})
        monday.write({'start_hour': 9.0, 'end_hour': 17.0})
        stage.write({'name': 'Pending'})
        self.env.flush_all()

        self.assertEqual(restore_install_keep(self.env), 3)
        self.assertEqual(schedule.name, 'Yoder Hours')
        self.assertEqual(schedule.timezone, 'US/Central')
        self.assertEqual((monday.start_hour, monday.end_hour), (7.5, 18.0))
        self.assertEqual(stage.name, 'Inbox')
        self.assertFalse(table_exists(cr, INSTALL_KEEP_TABLE))
        self.assertEqual(
            self.env['connect.schedule'].search_count([('name', '=', 'US Business Hours')]), 0)

    def test_restore_without_snapshot_is_a_noop(self):
        self.assertFalse(table_exists(self.env.cr, INSTALL_KEEP_TABLE))
        self.assertEqual(restore_install_keep(self.env), 0)
