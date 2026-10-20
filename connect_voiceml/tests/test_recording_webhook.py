# -*- coding: utf-8 -*-
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRecordingWebhook(TransactionCase):

    def _params(self):
        return {
            'RecordingSid': 'RE_f1_test',
            'CallSid': 'CA_f1_none',
            'RecordingDuration': '5',
            'RecordingStatus': 'completed',
        }

    def test_start_time_parsed_and_idempotent(self):
        rec = SimpleNamespace(
            sid='RE_f1_test', call_sid='CA_f1_none',
            media_url='https://voiceml.voicetel.com/rec/1.mp3',
            price=None, price_unit=None, duration=5, source=None,
            start_time='Mon, 19 Oct 2026 12:00:00 +0000', status='completed')
        client = MagicMock()
        client.recordings.get.return_value = rec
        Recording = self.env['connect.recording']
        with patch.object(type(self.env['connect.settings']), '_voiceml_client',
                          return_value=client):
            Recording._on_recording_status_voiceml(self._params())
            Recording._on_recording_status_voiceml(self._params())
        rows = Recording.sudo().search([('sid', '=', 'RE_f1_test')])
        self.assertEqual(len(rows), 1)
        self.assertEqual(str(rows.start_time), '2026-10-19 12:00:00')

    def test_fetch_failure_falls_back_to_webhook_url(self):
        client = MagicMock()
        client.recordings.get.side_effect = RuntimeError('boom')
        params = dict(self._params(), RecordingSid='RE_f1_fail',
                      RecordingUrl='https://voiceml.voicetel.com/rec/2')
        with patch.object(type(self.env['connect.settings']), '_voiceml_client',
                          return_value=client):
            self.env['connect.recording']._on_recording_status_voiceml(params)
        row = self.env['connect.recording'].sudo().search([('sid', '=', 'RE_f1_fail')])
        self.assertEqual(row.media_url, 'https://voiceml.voicetel.com/rec/2')
