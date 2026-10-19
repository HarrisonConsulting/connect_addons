import base64
import importlib.util
from pathlib import Path

from odoo.tests import TransactionCase, tagged
from odoo.tools.sql import column_exists


def _migration_module(version, filename):
    path = Path(__file__).parents[1] / 'migrations' / version / filename
    spec = importlib.util.spec_from_file_location(
        'connect_migration_%s' % version.replace('.', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@tagged('post_install', '-at_install')
class TestMigrationRecordingAttachment(TransactionCase):
    """A 1.30.x recording file survives the drop of attachment_id."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.migration = _migration_module('19.0.4.5.3', 'pre-migration.py')

    def setUp(self):
        super().setUp()
        cr = self.env.cr
        if not column_exists(cr, 'connect_recording', 'attachment_id'):
            # The pre-NG column, as the old schema had it.
            cr.execute(
                "ALTER TABLE connect_recording ADD COLUMN attachment_id integer"
                " REFERENCES ir_attachment(id) ON DELETE SET NULL")

    def _recording(self, **values):
        return self.env['connect.recording'].with_context(
            skip_transcription=True, recording_redaction=True).create(values)

    def _legacy_file(self, recording, payload):
        attachment = self.env['ir.attachment'].create({
            'name': 'recording_RE1.mp3',
            'raw': payload,
            'res_model': 'connect.recording',
            'res_id': recording.id,
            'mimetype': 'audio/mpeg',
        })
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE connect_recording SET attachment_id = %s WHERE id = %s",
            (attachment.id, recording.id))
        return attachment

    def test_legacy_file_becomes_the_field_attachment(self):
        recording = self._recording()
        attachment = self._legacy_file(recording, b'ID3-legacy')

        self.migration.migrate(self.env.cr, '19.0.1.30.13')
        self.env.invalidate_all()

        self.assertEqual(base64.b64decode(recording.recording_attachment), b'ID3-legacy')
        self.assertEqual(attachment.res_field, 'recording_attachment')
        self.assertEqual(attachment.res_id, recording.id)

    def test_existing_field_attachment_is_kept(self):
        recording = self._recording(recording_attachment=base64.b64encode(b'ID3-new'))
        attachment = self._legacy_file(recording, b'ID3-legacy')

        self.migration.migrate(self.env.cr, '19.0.1.30.13')
        self.env.invalidate_all()

        self.assertEqual(base64.b64decode(recording.recording_attachment), b'ID3-new')
        self.assertFalse(attachment.res_field)

    def test_shared_file_reaches_every_recording(self):
        first, second = self._recording(), self._recording()
        attachment = self._legacy_file(first, b'ID3-shared')
        self.env.cr.execute(
            "UPDATE connect_recording SET attachment_id = %s WHERE id = %s",
            (attachment.id, second.id))

        self.migration.migrate(self.env.cr, '19.0.1.30.13')
        self.env.invalidate_all()

        self.assertEqual(base64.b64decode(first.recording_attachment), b'ID3-shared')
        self.assertEqual(base64.b64decode(second.recording_attachment), b'ID3-shared')
