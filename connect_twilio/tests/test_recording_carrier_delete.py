# -*- coding: utf-8 -*-
"""Twilio deletes its copy of a recording once ours is verified. No HTTP leaves the test."""
from unittest.mock import MagicMock, patch

from twilio.base.exceptions import TwilioRestException

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestTwilioRecordingCarrierDelete(TransactionCase):

    def setUp(self):
        super().setUp()
        self.client = MagicMock()
        self.startPatcher(patch.object(
            type(self.env['connect.settings']), 'get_client', return_value=self.client))
        self.recording = self.env['connect.recording'].with_context(
            skip_transcription=True).create({
                'sid': 'RE00000000000000000000000000000001',
                'media_url': 'https://api.twilio.com/2010-04-01/Accounts/AC1/Recordings/RE1',
            })

    def test_twilio_hosts_are_twilio_recordings(self):
        self.assertTrue(self.recording._provider_owns_recording())
        self.recording.media_url = 'https://api.dublin.ie1.twilio.com/Recordings/RE1'
        self.assertTrue(self.recording._provider_owns_recording())

    def test_external_storage_and_lookalike_hosts_are_not(self):
        for url in ('https://bucket.s3.amazonaws.com/RE1.wav',
                    'https://twilio.com.example.test/RE1',
                    False):
            self.recording.media_url = url
            self.assertFalse(self.recording._provider_owns_recording(), url)

    def test_delete_calls_the_recording_resource(self):
        self.client.recordings.return_value.delete.return_value = True
        self.assertTrue(self.recording._provider_delete_recording())
        self.client.recordings.assert_called_once_with(self.recording.sid)

    def test_404_counts_as_deleted(self):
        self.client.recordings.return_value.delete.side_effect = TwilioRestException(
            404, '/Recordings/RE1.json', 'not found', method='DELETE')
        self.assertTrue(self.recording._provider_delete_recording())

    def test_other_errors_raise(self):
        self.client.recordings.return_value.delete.side_effect = TwilioRestException(
            500, '/Recordings/RE1.json', 'server error', method='DELETE')
        with self.assertRaises(TwilioRestException):
            self.recording._provider_delete_recording()

    def test_unconfirmed_delete_is_not_success(self):
        self.client.recordings.return_value.delete.return_value = False
        self.assertFalse(self.recording._provider_delete_recording())

    def test_sync_skips_a_deleted_recording(self):
        self.recording.write({'provider_deleted_at': '2026-11-10 12:00:00'})
        self.recording.sync()
        self.client.recordings.assert_not_called()
