# -*- coding: utf-8 -*-
"""Recordings are pulled into Odoo on the status callback (ADR-066 decision 7).

VoiceML keeps the WAV locally only until it is uploaded to the tenant's own
bucket or cleaned up, so the audio is stored on the record while the
callback runs; a failed download keeps the media URL for the proxy.
"""
from unittest.mock import patch

import requests

from odoo.tests import tagged

from .common import VoiceTelTestCommon

RECORDING_URL = 'https://voiceml.voicetel.com/2010-04-01/Accounts/AC1/Recordings/REdownload1'


class _Response:
    def __init__(self, content=b'RIFF....WAVEfmt ', status=200,
                 content_type='audio/x-wav', location=None):
        self.content = content
        self.status_code = status
        self.headers = {'Content-Type': content_type}
        if location:
            self.headers['Location'] = location
        self.is_redirect = bool(location)

    def iter_content(self, size):
        yield self.content

    def close(self):
        pass

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


@tagged('at_install', '-post_install')
class TestRecordingDownload(VoiceTelTestCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        settings = cls.env['connect.settings'].sudo()
        settings.set_param('voicetel_account_sid', 'AC1')
        settings.set_param('voicetel_api_key', 'key')
        settings.set_param('transcript_calls', False)
        cls.channel = cls.env['connect.channel'].sudo().create({
            'sid': 'CAdownload1',
            'caller': '+15550001111',
            'called': '+15550002222',
        })

    def _params(self, sid='REdownload1', status='completed'):
        return {
            'AccountSid': 'AC1',
            'CallSid': 'CAdownload1',
            'RecordingSid': sid,
            'RecordingUrl': RECORDING_URL,
            'RecordingStatus': status,
            'RecordingDuration': '12',
            'RecordingSource': 'DialVerb',
        }

    def test_completed_recording_is_stored_as_an_attachment(self):
        with patch.object(requests, 'get', return_value=_Response()) as get:
            self.env['connect.recording'].on_voicetel_recording_status(
                self._params())

        recording = self.env['connect.recording'].sudo().search(
            [('sid', '=', 'REdownload1')], limit=1)
        self.assertTrue(recording)
        self.assertTrue(recording.recording_attachment)
        self.assertEqual(recording.recording_filename, 'REdownload1.wav')
        self.assertEqual(recording.media_url, RECORDING_URL)
        self.assertEqual(recording.duration, 12)
        self.assertEqual(recording.channel, self.channel)
        # Fetched with the account credentials, not anonymously.
        self.assertEqual(get.call_args.kwargs.get('auth'), ('AC1', 'key'))

    def test_a_failed_download_keeps_the_media_url(self):
        with patch.object(requests, 'get', return_value=_Response(status=410)):
            self.env['connect.recording'].on_voicetel_recording_status(
                self._params(sid='REdownload2'))

        recording = self.env['connect.recording'].sudo().search(
            [('sid', '=', 'REdownload2')], limit=1)
        self.assertTrue(recording)
        self.assertFalse(recording.recording_attachment)
        self.assertEqual(recording.media_url, RECORDING_URL)

    def test_a_second_callback_for_the_same_sid_does_not_duplicate(self):
        with patch.object(requests, 'get', return_value=_Response()):
            self.env['connect.recording'].on_voicetel_recording_status(
                self._params(sid='REdownload3'))
            self.env['connect.recording'].on_voicetel_recording_status(
                self._params(sid='REdownload3'))

        self.assertEqual(
            self.env['connect.recording'].sudo().search_count(
                [('sid', '=', 'REdownload3')]), 1)

    def test_in_progress_callbacks_store_nothing(self):
        with patch.object(requests, 'get') as get:
            self.env['connect.recording'].on_voicetel_recording_status(
                self._params(sid='REdownload4', status='in-progress'))

        get.assert_not_called()
        self.assertFalse(self.env['connect.recording'].sudo().search(
            [('sid', '=', 'REdownload4')]))

    def test_redirect_to_the_bucket_carries_no_credentials(self):
        bucket = 'https://tenant-bucket.s3.amazonaws.com/REdownload5.wav?X-Amz-Signature=x'
        with patch.object(requests, 'get', side_effect=[
                _Response(status=302, location=bucket), _Response()]) as get:
            self.env['connect.recording'].on_voicetel_recording_status(
                self._params(sid='REdownload5'))
        first, second = get.call_args_list
        self.assertEqual(first.kwargs.get('auth'), ('AC1', 'key'))
        self.assertFalse(first.kwargs.get('allow_redirects'))
        self.assertEqual(second.args[0], bucket)
        self.assertIsNone(second.kwargs.get('auth'))
        recording = self.env['connect.recording'].sudo().search(
            [('sid', '=', 'REdownload5')], limit=1)
        self.assertTrue(recording.recording_attachment)

    def test_untrusted_recording_url_is_never_fetched(self):
        params = self._params(sid='REdownload6')
        params['RecordingUrl'] = 'https://evil.example.com/x.wav'
        with patch.object(requests, 'get') as get:
            self.env['connect.recording'].on_voicetel_recording_status(params)
        get.assert_not_called()
