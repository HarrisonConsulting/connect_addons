# -*- coding: utf-8 -*-
"""Tests for connect.recording model and transcription pipeline."""

import os
from unittest.mock import patch, MagicMock, PropertyMock

from odoo.tests import tagged
from .common import ConnectTestCase


class MockTwilioRecordingInstance:
    """Mock for a fetched Twilio recording resource."""

    def __init__(self, sid='RExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx',
                 call_sid='CAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx',
                 media_url='https://api.twilio.com/recordings/RExx.mp3',
                 **kwargs):
        self.sid = sid
        self.call_sid = call_sid
        self.media_url = media_url
        self.price = '-0.0025'
        self.price_unit = 'USD'
        self.duration = '30'
        self.source = 'DialVerb'
        self.start_time = None
        self.status = 'completed'
        for key, value in kwargs.items():
            setattr(self, key, value)

    def fetch(self):
        return self


@tagged('post_install', '-at_install')
class TestRecording(ConnectTestCase):
    """Test connect.recording CRUD and computed fields."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Recording = cls.env['connect.recording']

    def _create_recording(self, **kwargs):
        """Helper to create a recording with skip_transcription context."""
        vals = {
            'sid': kwargs.pop('sid', 'RE' + 'a' * 32),
            'call_sid': kwargs.pop('call_sid', 'CA' + 'b' * 32),
        }
        vals.update(kwargs)
        return self.Recording.with_context(skip_transcription=True).create(vals)

    def test_recording_create(self):
        """Test basic recording creation with required fields."""
        rec = self._create_recording()
        self.assertTrue(rec.id)
        self.assertEqual(rec.sid, 'RE' + 'a' * 32)
        self.assertEqual(rec.call_sid, 'CA' + 'b' * 32)

    def test_duration_human_format(self):
        """Test duration_human: 125 seconds formats as 02:05."""
        rec = self._create_recording(duration=125)
        self.assertEqual(rec.duration_human, '02:05')

    def test_duration_human_zero(self):
        """Test duration_human: 0 seconds formats as 00:00."""
        rec = self._create_recording(duration=0)
        self.assertEqual(rec.duration_human, '00:00')

    def test_recording_widget_with_media_url(self):
        """Test recording_widget generates an <audio> tag when media_url is set.

        proxy_recordings defaults to True, so the widget uses a proxied URL
        (/connect/recording/<id>) instead of the direct media_url.
        """
        rec = self._create_recording(media_url='https://example.com/audio.mp3')
        self.assertIn('<audio', rec.recording_widget)
        self.assertIn('/connect/recording/%s' % rec.id, rec.recording_widget)

    def test_recording_widget_without_media_url(self):
        """Test recording_widget returns empty string without media_url."""
        rec = self._create_recording()
        self.assertEqual(rec.recording_widget, '')


@tagged('post_install', '-at_install')
class TestOnRecordingStatus(ConnectTestCase):
    """Test the on_recording_status webhook handler."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Recording = cls.env['connect.recording']

    def _make_mock_client_with_recordings(self, rec_sid='RE' + 'c' * 32,
                                          call_sid='CA' + 'd' * 32):
        """Build a mock Twilio client whose .recordings(sid).fetch() works."""
        mock_recording = MockTwilioRecordingInstance(
            sid=rec_sid, call_sid=call_sid)

        mock_client = MagicMock()
        mock_client.recordings.return_value = mock_recording
        return mock_client

    def test_on_recording_status_creates_recording(self):
        """Webhook creates a recording linked to the correct call and channel."""
        call = self._create_test_call(
            direction='incoming', create_channel=True,
            partner=self.partner_1.id,
            caller='+15551234567', called='+15559876543')
        channel = call.channels[0]

        rec_sid = 'RE' + '1' * 32
        mock_client = self._make_mock_client_with_recordings(
            rec_sid=rec_sid, call_sid=channel.sid)

        params = {
            'RecordingSid': rec_sid,
            'CallSid': channel.sid,
            'RecordingDuration': '30',
            'RecordingStatus': 'completed',
        }

        with patch.object(
            self.env['connect.settings'].__class__, 'get_client',
            return_value=mock_client
        ):
            self.Recording.with_context(skip_transcription=True).on_recording_status(params)

        recording = self.Recording.search([('sid', '=', rec_sid)], limit=1)
        self.assertTrue(recording, "Recording should have been created")
        self.assertEqual(recording.call.id, call.id)
        self.assertEqual(recording.channel.id, channel.id)
        self.assertEqual(recording.caller_number, '+15551234567')
        self.assertEqual(recording.called_number, '+15559876543')

    def test_on_recording_status_duplicate_sid_idempotent(self):
        """Calling on_recording_status twice with the same RecordingSid creates only one recording."""
        call = self._create_test_call(
            direction='incoming', create_channel=True,
            caller='+15551234567', called='+15559876543')
        channel = call.channels[0]

        rec_sid = 'RE' + '2' * 32
        mock_client = self._make_mock_client_with_recordings(
            rec_sid=rec_sid, call_sid=channel.sid)

        params = {
            'RecordingSid': rec_sid,
            'CallSid': channel.sid,
            'RecordingDuration': '30',
            'RecordingStatus': 'completed',
        }

        with patch.object(
            self.env['connect.settings'].__class__, 'get_client',
            return_value=mock_client
        ):
            self.Recording.with_context(skip_transcription=True).on_recording_status(params)
            self.Recording.with_context(skip_transcription=True).on_recording_status(params)

        count = self.Recording.search_count([('sid', '=', rec_sid)])
        self.assertEqual(count, 1, "Duplicate webhook should not create a second recording")

    def test_on_recording_status_conference_fallback(self):
        """When no channel matches CallSid, falls back to conference_sid lookup."""
        call = self._create_test_call(direction='incoming')
        call.conference_sid = 'CF' + 'e' * 32

        rec_sid = 'RE' + '3' * 32
        # CallSid that does NOT match any channel
        unmatched_call_sid = 'CA' + 'f' * 32
        mock_client = self._make_mock_client_with_recordings(
            rec_sid=rec_sid, call_sid=unmatched_call_sid)

        params = {
            'RecordingSid': rec_sid,
            'CallSid': unmatched_call_sid,
            'RecordingDuration': '45',
            'RecordingStatus': 'completed',
            'ConferenceSid': 'CF' + 'e' * 32,
        }

        with patch.object(
            self.env['connect.settings'].__class__, 'get_client',
            return_value=mock_client
        ):
            self.Recording.with_context(skip_transcription=True).on_recording_status(params)

        recording = self.Recording.search([('sid', '=', rec_sid)], limit=1)
        self.assertTrue(recording, "Recording should have been created via conference fallback")
        self.assertEqual(recording.call.id, call.id)

    def test_on_recording_status_no_channel(self):
        """Recording is created even when no channel or conference matches."""
        rec_sid = 'RE' + '4' * 32
        orphan_call_sid = 'CA' + 'g' * 32

        mock_client = self._make_mock_client_with_recordings(
            rec_sid=rec_sid, call_sid=orphan_call_sid)

        params = {
            'RecordingSid': rec_sid,
            'CallSid': orphan_call_sid,
            'RecordingDuration': '10',
            'RecordingStatus': 'completed',
        }

        with patch.object(
            self.env['connect.settings'].__class__, 'get_client',
            return_value=mock_client
        ):
            self.Recording.with_context(skip_transcription=True).on_recording_status(params)

        recording = self.Recording.search([('sid', '=', rec_sid)], limit=1)
        self.assertTrue(recording, "Recording should be created even without matching channel")
        self.assertFalse(recording.call, "No call should be linked")
        self.assertFalse(recording.channel, "No channel should be linked")


@tagged('post_install', '-at_install')
class TestRecordingSummaryContext(ConnectTestCase):
    """Test summary context building and prompt rendering."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Recording = cls.env['connect.recording']

    def _create_recording(self, **kwargs):
        vals = {
            'sid': kwargs.pop('sid', 'RE' + 'x' * 32),
            'call_sid': kwargs.pop('call_sid', 'CA' + 'y' * 32),
        }
        vals.update(kwargs)
        return self.Recording.with_context(skip_transcription=True).create(vals)

    def test_get_summary_context_with_partner(self):
        """caller_name comes from the call's partner when available."""
        call = self._create_test_call(
            direction='incoming', partner=self.partner_1.id,
            caller='+15551234567', called='+15559876543')
        rec = self._create_recording(
            call=call.id,
            caller_number='+15551234567',
            called_number='+15559876543')

        ctx = rec._get_summary_context()
        self.assertEqual(ctx['caller_name'], self.partner_1.display_name)
        self.assertEqual(ctx['direction'], 'incoming')

    def test_get_summary_context_without_partner(self):
        """caller_name falls back to caller_number when no partner."""
        call = self._create_test_call(
            direction='outgoing',
            caller='+15551234567', called='+15559876543')
        rec = self._create_recording(
            call=call.id,
            caller_number='+15551234567',
            called_number='+15559876543')

        ctx = rec._get_summary_context()
        self.assertEqual(ctx['caller_name'], '+15551234567')
        self.assertEqual(ctx['direction'], 'outgoing')

    def test_get_summary_context_no_call(self):
        """Context without a linked call returns safe defaults."""
        rec = self._create_recording(caller_number='+15550001111')
        ctx = rec._get_summary_context()
        self.assertEqual(ctx['caller_name'], '+15550001111')
        self.assertEqual(ctx['direction'], 'unknown')

    def test_render_summary_prompt_with_placeholders(self):
        """Known placeholders are replaced correctly in the prompt."""
        call = self._create_test_call(
            direction='incoming', partner=self.partner_1.id,
            caller='+15551234567', called='+15559876543')
        rec = self._create_recording(
            call=call.id,
            caller_number='+15551234567',
            called_number='+15559876543')

        template = "Summarize the call from {caller_name} to {called_number}."
        result = rec._render_summary_prompt(template, transcript='Hello world')
        self.assertIn(self.partner_1.display_name, result)
        self.assertIn('+15559876543', result)
        self.assertNotIn('{caller_name}', result)

    def test_render_summary_prompt_unknown_placeholder(self):
        """Unknown placeholders are left as-is (SafeDict behavior)."""
        rec = self._create_recording()
        template = "Summary for {foo} and {bar}: {transcript}"
        result = rec._render_summary_prompt(template, transcript='test transcript')
        self.assertIn('{foo}', result)
        self.assertIn('{bar}', result)
        self.assertIn('test transcript', result)

    def test_render_summary_prompt_transcript_placeholder(self):
        """The {transcript} placeholder is injected from the transcript arg."""
        rec = self._create_recording()
        template = "Please summarize:\n{transcript}"
        result = rec._render_summary_prompt(template, transcript='The quick brown fox.')
        self.assertIn('The quick brown fox.', result)
        self.assertNotIn('{transcript}', result)


@tagged('post_install', '-at_install')
class TestRecordingTranscription(ConnectTestCase):
    """Test the transcription pipeline with mocked external services."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Recording = cls.env['connect.recording']

    def _create_recording(self, **kwargs):
        vals = {
            'sid': kwargs.pop('sid', 'RE' + 't' * 32),
            'call_sid': kwargs.pop('call_sid', 'CA' + 'u' * 32),
        }
        vals.update(kwargs)
        return self.Recording.with_context(skip_transcription=True).create(vals)

    def test_transcribe_recording_no_media_url(self):
        """Transcription sets error when media_url is missing."""
        rec = self._create_recording()
        self.assertFalse(rec.media_url)

        rec.transcribe_recording('fake-key', 'Summarize this call.')
        self.assertEqual(rec.transcription_error, 'Recording media not available')
        self.assertFalse(rec.transcript)

    def test_transcribe_recording_file_too_large(self):
        """Transcription sets error when downloaded file exceeds 26MB."""
        rec = self._create_recording(media_url='https://example.com/large.mp3')

        # Mock _download_recording_audio to return a temp file path
        # Mock os.path.getsize to return >26MB
        with patch.object(
            rec.__class__, '_download_recording_audio',
            return_value='/tmp/fake_large_audio.mp3'
        ), patch('os.path.getsize', return_value=27_000_000), \
                patch('os.path.exists', return_value=True), \
                patch('os.remove'):
            rec.transcribe_recording('fake-key', 'Summarize this call.')

        self.assertIn('size limit', rec.transcription_error)
        self.assertFalse(rec.transcript)

    def test_transcribe_recording_success(self):
        """Full transcription pipeline: download, transcribe, summarize."""
        rec = self._create_recording(media_url='https://example.com/audio.mp3')

        mock_openai = MagicMock()
        # Mock whisper transcription: verbose_json response with segments
        mock_segment = MagicMock()
        mock_segment.start = 0.0
        mock_segment.text = ' Hello, this is a test.'
        mock_transcript_response = MagicMock()
        mock_transcript_response.segments = [mock_segment]
        mock_openai.audio.transcriptions.create.return_value = mock_transcript_response

        # Mock chat completion for summary
        mock_choice = MagicMock()
        mock_choice.message.content = 'Test call summary.'
        mock_completion = MagicMock()
        mock_completion.choices = [mock_choice]
        mock_completion.usage = MagicMock()
        mock_openai.chat.completions.create.return_value = mock_completion

        with patch.object(
            rec.__class__, '_download_recording_audio',
            return_value='/tmp/fake_audio.mp3'
        ), patch('os.path.getsize', return_value=1000), \
                patch('os.path.exists', return_value=True), \
                patch('os.remove'), \
                patch('builtins.open', MagicMock()), \
                patch.object(
                    self.env['connect.settings'].__class__,
                    'get_openai_client',
                    return_value=mock_openai):
            rec.transcribe_recording('fake-key', 'Summarize: {transcript}')

        self.assertTrue(rec.transcript)
        self.assertIn('Hello, this is a test', rec.transcript)
        # summary is an Html field; Odoo wraps plain text in <p> tags
        self.assertIn('Test call summary.', str(rec.summary))
        self.assertFalse(rec.transcription_error)

    def test_transcribe_recording_api_error(self):
        """Transcription error from OpenAI is captured, not raised."""
        rec = self._create_recording(media_url='https://example.com/audio.mp3')

        mock_openai = MagicMock()
        mock_openai.audio.transcriptions.create.side_effect = Exception('API rate limited')

        with patch.object(
            rec.__class__, '_download_recording_audio',
            return_value='/tmp/fake_audio.mp3'
        ), patch('os.path.getsize', return_value=1000), \
                patch('os.path.exists', return_value=True), \
                patch('os.remove'), \
                patch('builtins.open', MagicMock()), \
                patch.object(
                    self.env['connect.settings'].__class__,
                    'get_openai_client',
                    return_value=mock_openai):
            rec.transcribe_recording('fake-key', 'Summarize.')

        self.assertIn('API rate limited', rec.transcription_error)
        self.assertFalse(rec.transcript)

    def test_call_transcription_api_fallback_to_text(self):
        """When verbose_json fails, falls back to plain text format."""
        rec = self._create_recording(media_url='https://example.com/audio.mp3')

        mock_openai = MagicMock()
        # First call (verbose_json) raises, second call (text) succeeds
        mock_openai.audio.transcriptions.create.side_effect = [
            Exception('verbose_json not supported'),
            'Plain text transcript from fallback.',
        ]

        with patch('builtins.open', MagicMock()):
            result = rec._call_transcription_api(mock_openai, '/tmp/fake.mp3')

        self.assertEqual(result, 'Plain text transcript from fallback.')
        self.assertEqual(mock_openai.audio.transcriptions.create.call_count, 2)

    def test_make_summary_with_transcript_placeholder(self):
        """Summary uses transcript embedded in prompt via {transcript} placeholder."""
        rec = self._create_recording()

        mock_openai = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = 'Generated summary.'
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = MagicMock()
        mock_openai.chat.completions.create.return_value = mock_response

        result = rec.make_summary(
            mock_openai,
            'Summarize this call:\n{transcript}',
            'Hello, how are you?'
        )

        self.assertEqual(result['summary'], 'Generated summary.')
        # Verify single message (transcript embedded in prompt)
        call_args = mock_openai.chat.completions.create.call_args
        messages = call_args.kwargs.get('messages', call_args[1].get('messages', []))
        self.assertEqual(len(messages), 1)

    def test_make_summary_legacy_separate_messages(self):
        """Legacy prompt without {transcript} sends two separate messages."""
        rec = self._create_recording()

        mock_openai = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = 'Legacy summary.'
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = MagicMock()
        mock_openai.chat.completions.create.return_value = mock_response

        result = rec.make_summary(
            mock_openai,
            'Please summarize the following call.',
            'Hello, how are you?'
        )

        self.assertEqual(result['summary'], 'Legacy summary.')
        call_args = mock_openai.chat.completions.create.call_args
        messages = call_args.kwargs.get('messages', call_args[1].get('messages', []))
        self.assertEqual(len(messages), 2, "Legacy path should send prompt and transcript as separate messages")

    def test_make_summary_empty_recordset_uses_generic_context(self):
        """Voicemail summaries may call make_summary() without a recording row."""
        mock_openai = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = 'Generic summary.'
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = MagicMock()
        mock_openai.chat.completions.create.return_value = mock_response

        result = self.Recording.make_summary(
            mock_openai,
            'Summarize this {direction} call from {caller_name}: {transcript}',
            'Please call me back.'
        )

        self.assertEqual(result['summary'], 'Generic summary.')
        call_args = mock_openai.chat.completions.create.call_args
        messages = call_args.kwargs.get('messages', call_args[1].get('messages', []))
        self.assertEqual(len(messages), 1)
        self.assertIn('unknown', messages[0]['content'])
        self.assertIn('Unknown Caller', messages[0]['content'])
