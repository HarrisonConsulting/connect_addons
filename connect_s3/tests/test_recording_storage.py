import base64
from io import BytesIO
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestNgRecordingStorage(TransactionCase):
    def setUp(self):
        super().setUp()
        self.settings = self.env['connect.settings'].search([], limit=1)
        if not self.settings:
            self.settings = self.env['connect.settings'].create({})
        self.settings.write({'recording_storage': 's3', 's3_bucket': 'test-recordings'})

    def _recording(self, **values):
        values.setdefault('recording_attachment', base64.b64encode(b'RIFFtestaudio'))
        values.setdefault('status', 'completed')
        return self.env['connect.recording'].with_context(skip_transcription=True).create(values)

    def test_create_queues_storage_without_network(self):
        with patch.object(type(self.settings), 'get_s3_client') as client:
            recording = self._recording()
        client.assert_not_called()
        self.assertTrue(recording.storage_pending)

    def test_archive_verifies_bytes_and_is_idempotent(self):
        recording = self._recording()
        client = MagicMock()
        client.head_object.return_value = {'ContentLength': 13}
        with patch.object(type(self.settings), 'get_s3_client', return_value=client):
            recording._migrate_to_s3(self.settings)
            recording._migrate_to_s3(self.settings)
        self.assertTrue(recording.s3_key)
        self.assertFalse(recording.storage_pending)
        client.upload_fileobj.assert_called_once()
        client.head_object.assert_called_once()

    def test_verification_failure_keeps_local_audio_and_provider_source(self):
        recording = self._recording(media_url='https://api.twilio.com/recording.wav')
        client = MagicMock()
        client.head_object.return_value = {'ContentLength': 0}
        with patch.object(type(self.settings), 'get_s3_client', return_value=client):
            with self.assertRaisesRegex(ValueError, 'stored size'):
                recording._migrate_to_s3(self.settings)
        self.assertFalse(recording.s3_key)
        self.assertEqual(base64.b64decode(recording.recording_attachment), b'RIFFtestaudio')
        self.assertTrue(recording.media_url)
        client.delete_object.assert_not_called()

    def test_partial_s3_read_does_not_prefix_fallback(self):
        recording = self._recording(s3_key='existing.wav')
        client = MagicMock()

        def interrupted(bucket, key, output):
            output.write(b'partial')
            raise ClientError({'Error': {'Code': '503', 'Message': 'unavailable'}}, 'GetObject')

        client.download_fileobj.side_effect = interrupted
        with patch.object(type(self.settings), 'get_s3_client', return_value=client):
            output = BytesIO()
            recording._fetch_media_to(output)
        self.assertEqual(output.getvalue(), b'RIFFtestaudio')

    def test_playback_retains_provider_fallback_until_archived(self):
        recording = self._recording(recording_attachment=False,
                                    media_url='https://api.twilio.com/recording.wav')
        self.assertEqual(recording._get_media_src(True), '/connect/recording/%s' % recording.id)

    def test_failed_presign_retains_provider_playback(self):
        recording = self._recording(recording_attachment=False, s3_key='existing.wav',
                                    media_url='https://api.twilio.com/recording.wav')
        client = MagicMock()
        client.generate_presigned_url.side_effect = ValueError('invalid endpoint')
        with patch.object(type(self.settings), 'get_s3_client', return_value=client):
            self.assertEqual(recording._get_media_src(True), '/connect/recording/%s' % recording.id)

    def test_cleanup_attempts_each_deleted_recording(self):
        recordings = self._recording(s3_key='first.wav') | self._recording(s3_key='second.wav')
        client = MagicMock()
        client.delete_object.side_effect = [ValueError('first deletion failed'), None]
        with patch.object(type(self.settings), 'get_s3_client', return_value=client), \
                self.assertLogs('odoo.addons.connect_s3.models.recording', level='ERROR'):
            recordings.unlink()
        self.assertEqual(client.delete_object.call_count, 2)
        self.assertFalse(recordings.exists())
