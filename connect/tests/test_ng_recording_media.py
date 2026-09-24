import base64
import os
from io import BytesIO
from unittest.mock import MagicMock, patch

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestNgRecordingMedia(TransactionCase):
    def _recording(self, **values):
        return self.env['connect.recording'].with_context(skip_transcription=True).create(values)

    def test_materialized_attachment_reaches_file_consumers(self):
        recording = self._recording(recording_attachment=base64.b64encode(b'RIFFaudio'))
        path = recording._download_recording_audio()
        try:
            with open(path, 'rb') as audio:
                self.assertEqual(audio.read(), b'RIFFaudio')
        finally:
            os.unlink(path)

    def test_provider_media_uses_provider_auth_and_closes_response(self):
        recording = self._recording(media_url='https://api.twilio.com/recording.wav')
        response = MagicMock()
        response.__enter__.return_value = response
        response.iter_content.return_value = [b'RIFF', b'audio']
        with patch.object(type(self.env['connect.settings']), 'get_media_auth',
                          return_value=('account', 'secret')) as auth, \
                patch('odoo.addons.connect.models.recording.requests.get', return_value=response) as get:
            data = BytesIO()
            recording._fetch_media_to(data)
        self.assertEqual(data.getvalue(), b'RIFFaudio')
        auth.assert_called_once_with(recording.media_url)
        self.assertEqual(get.call_args.kwargs['auth'], ('account', 'secret'))
        response.__exit__.assert_called_once()

    def test_failed_materialization_removes_partial_file(self):
        recording = self._recording()
        paths = []

        def fail(_recording, media):
            paths.append(media.name)
            media.write(b'partial')
            raise ValueError('download failed')

        with patch.object(type(recording), '_fetch_media_to', fail):
            with self.assertRaisesRegex(ValueError, 'download failed'):
                recording._download_recording_audio()
        self.assertTrue(paths)
        self.assertFalse(os.path.exists(paths[0]))
