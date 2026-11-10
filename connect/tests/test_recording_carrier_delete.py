# -*- coding: utf-8 -*-
"""A recording is archived, verified, and only then deleted at the carrier."""

import base64
from unittest.mock import patch

import requests

from odoo.tests import TransactionCase, tagged

RIFF = b'RIFF\x24\x00\x00\x00WAVEfmt carrier-audio'


@tagged('post_install', '-at_install')
class TestRecordingCarrierDelete(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Recording = self.env['connect.recording'].with_context(skip_transcription=True)
        settings = self.env['connect.settings'].sudo()
        settings.set_param('recording_storage', 'odoo_filestore')
        settings.set_param('delete_provider_recording', True)
        self.events = []
        self.deleted = []
        self.delete_result = True
        RecordingClass = type(self.env['connect.recording'])
        self.startPatcher(patch.object(
            RecordingClass, '_provider_owns_recording', autospec=True,
            side_effect=lambda rec: (rec.media_url or '').startswith('https://carrier.test/')))
        self.startPatcher(patch.object(
            RecordingClass, '_provider_delete_recording', autospec=True,
            side_effect=self._carrier_delete))
        self.startPatcher(patch.object(
            RecordingClass, '_fetch_media_to', autospec=True,
            side_effect=self._carrier_download))
        self.startPatcher(patch.object(
            type(self.env['ir.cron']), '_commit_progress', autospec=True,
            side_effect=lambda cron, processed=0, **kw: self.events.append('commit') or 60.0))
        self.download = RIFF
        self.serial = 0

    def _carrier_delete(self, rec):
        self.events.append('delete')
        self.deleted.append(rec.sid)
        if isinstance(self.delete_result, Exception):
            raise self.delete_result
        return self.delete_result

    def _carrier_download(self, rec, temp_file):
        if rec.recording_attachment:
            temp_file.write(base64.b64decode(rec.recording_attachment))
            return
        self.events.append('download')
        if isinstance(self.download, Exception):
            raise self.download
        temp_file.write(self.download)

    def _recording(self, **values):
        self.serial += 1
        values.setdefault('sid', 'RE%032d' % self.serial)
        values.setdefault('status', 'completed')
        values.setdefault('media_url', 'https://carrier.test/%s' % values['sid'])
        return self.Recording.create(values)

    def _run(self):
        self.env['connect.recording']._cron_delete_provider_recordings()

    def _run_kept(self):
        """Run the cron where the carrier copy must be kept, and own its warning."""
        with self.assertLogs('odoo.addons.connect.models.recording', level='WARNING') as logs:
            self._run()
        self.assertIn('Carrier copy kept', logs.output[0])

    def _attachment(self, rec):
        return self.env['ir.attachment'].sudo().search([
            ('res_model', '=', 'connect.recording'),
            ('res_field', '=', 'recording_attachment'),
            ('res_id', '=', rec.id),
        ])

    def test_callback_queues_without_touching_the_carrier(self):
        rec = self._recording()
        self.assertTrue(rec.provider_delete_pending)
        self.assertEqual(self.events, [])

    def test_ingest_commits_then_verifies_then_deletes(self):
        rec = self._recording()
        self._run()
        self.assertEqual(base64.b64decode(rec.recording_attachment), RIFF)
        self.assertTrue(self._attachment(rec).checksum)
        self.assertEqual(self.deleted, [rec.sid])
        self.assertEqual(self.events[:3], ['download', 'commit', 'delete'])
        self.assertTrue(rec.provider_deleted_at)
        self.assertFalse(rec.provider_delete_pending)
        self.assertFalse(rec.provider_delete_error)

    def test_already_stored_attachment_is_verified_and_deleted(self):
        rec = self._recording(recording_attachment=base64.b64encode(RIFF))
        self._run()
        self.assertNotIn('download', self.events)
        self.assertEqual(self.deleted, [rec.sid])
        self.assertTrue(rec.provider_deleted_at)

    def test_no_delete_when_the_stored_copy_fails_its_checksum(self):
        rec = self._recording(recording_attachment=base64.b64encode(RIFF))
        attachment = self._attachment(rec)
        self.env.cr.execute(
            'UPDATE ir_attachment SET checksum = %s WHERE id = %s', ['0' * 40, attachment.id])
        attachment.invalidate_recordset(['checksum'])
        self._run_kept()
        self.assertEqual(self.deleted, [])
        self.assertFalse(rec.provider_deleted_at)
        self.assertIn('checksum', rec.provider_delete_error)
        self.assertEqual(rec.provider_delete_attempts, 1)
        self.assertTrue(rec.provider_delete_pending)

    def test_no_delete_when_the_stored_copy_reads_back_empty(self):
        rec = self._recording(recording_attachment=base64.b64encode(RIFF))
        with patch.object(type(self.env['ir.attachment']), '_file_read',
                          autospec=True, return_value=b''):
            self._run_kept()
        self.assertEqual(self.deleted, [])
        self.assertIn('empty', rec.provider_delete_error)

    def test_no_delete_when_the_download_fails(self):
        self.download = requests.ConnectionError('carrier unreachable')
        rec = self._recording()
        self._run_kept()
        self.assertEqual(self.deleted, [])
        self.assertFalse(rec.recording_attachment)
        self.assertFalse(rec.provider_deleted_at)
        self.assertIn('carrier unreachable', rec.provider_delete_error)

    def test_no_delete_when_the_download_is_empty(self):
        self.download = b''
        rec = self._recording()
        self._run_kept()
        self.assertEqual(self.deleted, [])
        self.assertFalse(rec.provider_deleted_at)

    def test_unconfirmed_delete_is_not_recorded_as_deleted(self):
        self.delete_result = False
        rec = self._recording()
        self._run_kept()
        self.assertEqual(self.deleted, [rec.sid])
        self.assertFalse(rec.provider_deleted_at)
        self.assertTrue(rec.provider_delete_error)

    def test_setting_off_neither_queues_nor_deletes(self):
        self.env['connect.settings'].sudo().set_param('delete_provider_recording', False)
        rec = self._recording()
        self.assertFalse(rec.provider_delete_pending)
        rec.write({'provider_delete_pending': True})
        self._run()
        self.assertEqual(self.events, [])
        self.assertFalse(rec.provider_deleted_at)

    def test_neutralized_copy_never_deletes(self):
        self.env['ir.config_parameter'].sudo().set_param('database.is_neutralized', True)
        rec = self._recording()
        self.assertFalse(rec.provider_delete_pending)
        rec.write({'provider_delete_pending': True})
        self._run()
        self.assertEqual(self.deleted, [])

    def test_recordings_kept_at_the_carrier_by_choice_are_not_queued(self):
        # recording_storage = 'twilio' is the one choice with no archive of ours.
        with patch.object(type(self.env['connect.recording']), '_archive_mode',
                          autospec=True, return_value=None):
            rec = self._recording()
        self.assertFalse(rec.provider_delete_pending)

    def test_unfinished_or_foreign_recordings_are_not_queued(self):
        self.assertFalse(self._recording(status='in-progress').provider_delete_pending)
        self.assertFalse(self._recording(
            media_url='https://elsewhere.test/a.wav').provider_delete_pending)

    def test_unsilenced_marks_hold_the_carrier_copy(self):
        rec = self._recording(recording_attachment=base64.b64encode(b'ID3 not a wav'))
        with patch.object(type(rec), '_redaction_spans', autospec=True,
                          return_value=[(0, 1000)]):
            self._run_kept()
        self.assertEqual(self.deleted, [])
        self.assertIn('marks', rec.provider_delete_error)

    def test_failures_stop_retrying_and_retry_starts_again(self):
        self.delete_result = requests.Timeout('no answer')
        rec = self._recording(recording_attachment=base64.b64encode(RIFF))
        for _attempt in range(5):
            self._run_kept()
        self._run()
        self.assertEqual(len(self.deleted), 5)
        self.assertFalse(rec.provider_delete_pending)
        self.assertEqual(rec.provider_delete_attempts, 5)
        self.delete_result = True
        rec.action_retry_provider_delete()
        self.assertTrue(rec.provider_delete_pending)
        self.assertEqual(rec.provider_delete_attempts, 0)
        self.assertFalse(rec.provider_delete_error)
        self._run()
        self.assertTrue(rec.provider_deleted_at)

    def test_a_deleted_recording_is_never_deleted_twice(self):
        rec = self._recording(recording_attachment=base64.b64encode(RIFF))
        self._run()
        rec.action_retry_provider_delete()
        rec.write({'status': 'completed'})
        self._run()
        self.assertEqual(self.deleted, [rec.sid])
        self.assertFalse(rec.provider_delete_pending)


def _migration():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).parents[1] / 'migrations' / '19.0.4.6.13' / 'post-migration.py'
    spec = importlib.util.spec_from_file_location('connect_carrier_delete_migration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@tagged('post_install', '-at_install')
class TestCarrierDeleteMigration(TransactionCase):

    def setUp(self):
        super().setUp()
        self.settings = self.env['connect.settings'].sudo()
        self.icp = self.env['ir.config_parameter'].sudo()
        self.settings.set_param('delete_provider_recording', False)
        self.icp.set_param('connect.delete_twilio_recording', 'False')

    def test_existing_database_turns_it_on(self):
        _migration().migrate(self.env.cr, '19.0.4.6.12')
        self.env.invalidate_all()
        self.assertTrue(self.settings.get_param('delete_provider_recording'))
        self.assertEqual(self.icp.get_param('connect.delete_provider_recording'), 'True')
        self.assertFalse(self.icp.get_param('connect.delete_twilio_recording'))

    def test_neutralized_copy_turns_it_off(self):
        self.icp.set_param('database.is_neutralized', True)
        _migration().migrate(self.env.cr, '19.0.4.6.12')
        self.env.invalidate_all()
        self.assertFalse(self.settings.get_param('delete_provider_recording'))

    def test_new_install_defaults_on(self):
        self.assertTrue(self.env['connect.settings']._fields['delete_provider_recording'].default(
            self.env['connect.settings']))
