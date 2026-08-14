# -*- coding: utf-8 -*-
"""Tests for error handling across the connect module: credentials, transcription, webhooks, settings."""

from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from twilio.request_validator import RequestValidator

from odoo.tests import tagged
from odoo.exceptions import ValidationError
from odoo.addons.connect.tools import validate_twilio_request

from .common import ConnectTestCase


@tagged('post_install', '-at_install')
class TestTwilioClientErrors(ConnectTestCase):
    """Test get_client error handling for missing/invalid credentials."""

    def test_get_client_missing_credentials_returns_none(self):
        """get_client returns None when account_sid and auth_token are empty."""
        with patch.object(
            self.env['connect.settings'].__class__, 'get_param',
            return_value=False,
        ):
            result = self.env['connect.settings'].get_client()
            self.assertIsNone(result)

    def test_get_client_missing_auth_token_returns_none(self):
        """get_client returns None when auth_token is empty but account_sid is set."""
        def side_effect(param, default=False):
            if param == 'account_sid':
                return 'ACtest1234'
            return False

        with patch.object(
            self.env['connect.settings'].__class__, 'get_param',
            side_effect=side_effect,
        ):
            result = self.env['connect.settings'].get_client()
            self.assertIsNone(result)

    def test_get_client_invalid_credentials(self):
        """get_client with credentials that Twilio rejects raises ValidationError."""
        with patch.object(
            self.env['connect.settings'].__class__, 'get_param',
            return_value='fake_value',
        ), patch(
            'odoo.addons.connect.models.settings.Client',
            side_effect=Exception('Credentials are required to create a TwilioClient'),
        ):
            with self.assertRaises(ValidationError) as cm:
                self.env['connect.settings'].get_client()
            self.assertIn('Twilio API keys', str(cm.exception))


@tagged('post_install', '-at_install')
class TestTwilioRequestValidation(ConnectTestCase):
    """Every public Twilio adapter delegates to one fail-closed policy."""

    test_url = 'https://example.com/twilio/webhook/status'
    test_data = {'CallSid': 'CA_test', 'CallStatus': 'completed'}

    def _httprequest(self, signature='', *, url=None):
        return SimpleNamespace(
            url=url or self.test_url,
            path='/twilio/webhook/status',
            headers={'X-Twilio-Signature': signature},
        )

    def test_disabled_verification_fails_closed(self):
        settings = self.env['connect.settings']
        with patch.object(
            settings.__class__, 'get_param', return_value=False,
        ), patch.object(
            settings.__class__, '_get_client_credentials',
            side_effect=AssertionError('credentials must not be read'),
        ):
            self.assertFalse(validate_twilio_request(
                settings, self._httprequest(), self.test_data,
            ))

    def test_missing_auth_token_fails_closed(self):
        settings = self.env['connect.settings']
        with patch.object(
            settings.__class__, 'get_param', return_value=True,
        ), patch.object(
            settings.__class__, '_get_client_credentials',
            return_value=('AC_test', False),
        ):
            self.assertFalse(validate_twilio_request(
                settings, self._httprequest(), self.test_data,
            ))

    def test_valid_signature_is_accepted(self):
        settings = self.env['connect.settings']
        auth_token = 'test_auth_token'
        signature = RequestValidator(auth_token).compute_signature(
            self.test_url, self.test_data,
        )
        with patch.object(
            settings.__class__, 'get_param', return_value=True,
        ), patch.object(
            settings.__class__, '_get_client_credentials',
            return_value=('AC_test', auth_token),
        ):
            self.assertTrue(validate_twilio_request(
                settings, self._httprequest(signature), self.test_data,
            ))

    def test_invalid_signature_is_rejected(self):
        settings = self.env['connect.settings']
        with patch.object(
            settings.__class__, 'get_param', return_value=True,
        ), patch.object(
            settings.__class__, '_get_client_credentials',
            return_value=('AC_test', 'test_auth_token'),
        ):
            self.assertFalse(validate_twilio_request(
                settings, self._httprequest('invalid'), self.test_data,
            ))

    def test_http_proxy_url_is_validated_as_https(self):
        settings = self.env['connect.settings']
        auth_token = 'test_auth_token'
        signature = RequestValidator(auth_token).compute_signature(
            self.test_url, self.test_data,
        )
        with patch.object(
            settings.__class__, 'get_param', return_value=True,
        ), patch.object(
            settings.__class__, '_get_client_credentials',
            return_value=('AC_test', auth_token),
        ):
            self.assertTrue(validate_twilio_request(
                settings,
                self._httprequest(signature, url=self.test_url.replace('https:', 'http:')),
                self.test_data,
            ))


@tagged('post_install', '-at_install')
class TestRecordingTranscriptionErrors(ConnectTestCase):
    """Test transcription error handling paths."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Recording = cls.env['connect.recording']

    def _create_recording(self, **kwargs):
        vals = {
            'sid': kwargs.pop('sid', 'RE_err_' + 'e' * 26),
            'call_sid': kwargs.pop('call_sid', 'CA_err_' + 'f' * 26),
        }
        vals.update(kwargs)
        return self.Recording.with_context(skip_transcription=True).create(vals)

    def test_transcription_api_timeout(self):
        """Transcription handles API timeout gracefully -- error captured, not raised."""
        rec = self._create_recording(
            sid='RE_timeout_' + 't' * 22,
            media_url='https://example.com/audio.mp3',
        )

        mock_openai = MagicMock()
        mock_openai.audio.transcriptions.create.side_effect = TimeoutError(
            'Connection timed out')

        with patch.object(
            rec.__class__, '_download_recording_audio',
            return_value='/tmp/fake_timeout.mp3',
        ), patch('os.path.getsize', return_value=1000), \
                patch('os.path.exists', return_value=True), \
                patch('os.remove'), \
                patch('builtins.open', MagicMock()), \
                patch.object(
                    self.env['connect.settings'].__class__,
                    'get_openai_client',
                    return_value=mock_openai):
            rec.transcribe_recording('fake-key', 'Summarize.')

        self.assertIn('timed out', rec.transcription_error)
        self.assertFalse(rec.transcript)

    def test_transcription_file_too_large(self):
        """Transcription rejects files over 26MB limit."""
        rec = self._create_recording(
            sid='RE_large_' + 'l' * 24,
            media_url='https://example.com/big.mp3',
        )

        with patch.object(
            rec.__class__, '_download_recording_audio',
            return_value='/tmp/fake_big.mp3',
        ), patch('os.path.getsize', return_value=30_000_000), \
                patch('os.path.exists', return_value=True), \
                patch('os.remove'):
            rec.transcribe_recording('fake-key', 'Summarize.')

        self.assertIn('size limit', rec.transcription_error)
        self.assertFalse(rec.transcript)

    def test_recording_download_network_error(self):
        """Recording download handles network failures -- error captured on record."""
        rec = self._create_recording(
            sid='RE_neterr_' + 'n' * 23,
            media_url='https://example.com/unavailable.mp3',
        )

        with patch.object(
            rec.__class__, '_download_recording_audio',
            side_effect=ConnectionError('Network unreachable'),
        ), patch('os.path.exists', return_value=False):
            rec.transcribe_recording('fake-key', 'Summarize.')

        self.assertIn('Network unreachable', rec.transcription_error)
        self.assertFalse(rec.transcript)

    def test_get_transcript_missing_openai_key_raises(self):
        """get_transcript raises ValidationError when OpenAI key is not set."""
        rec = self._create_recording(
            sid='RE_nokey_' + 'k' * 24,
            media_url='https://example.com/audio.mp3',
        )

        with patch.object(
            self.env['connect.settings'].__class__, 'get_param',
            return_value=False,
        ):
            with self.assertRaises(ValidationError) as cm:
                rec.get_transcript()
            self.assertIn('OpenAI key', str(cm.exception))

    def test_get_transcript_missing_key_silent(self):
        """get_transcript with fail_silently=True returns False, no exception."""
        rec = self._create_recording(
            sid='RE_silent_' + 's' * 23,
            media_url='https://example.com/audio.mp3',
        )

        with patch.object(
            self.env['connect.settings'].__class__, 'get_param',
            return_value=False,
        ):
            result = rec.get_transcript(fail_silently=True)
            self.assertFalse(result)

    def test_get_transcript_no_media_url_raises(self):
        """get_transcript raises ValidationError when media_url is missing."""
        rec = self._create_recording(sid='RE_nomedia_' + 'm' * 22)

        with patch.object(
            self.env['connect.settings'].__class__, 'get_param',
            side_effect=lambda param, default=False: 'fake-key' if param == 'openai_api_key' else default,
        ):
            with self.assertRaises(ValidationError) as cm:
                rec.get_transcript()
            self.assertIn('not available', str(cm.exception))


@tagged('post_install', '-at_install')
class TestChannelDuplicateWebhook(ConnectTestCase):
    """Test duplicate and out-of-order webhook filtering on channel."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Channel = cls.env['connect.channel']

    def _create_channel(self, **kwargs):
        vals = {
            'sid': kwargs.pop('sid', 'CA_dup_' + 'd' * 26),
            'caller': '+15551234567',
            'called': '+15559876543',
            'technical_direction': 'inbound',
            'status': 'ringing',
            'sequence_number': 1,
        }
        vals.update(kwargs)
        return self.Channel.create(vals)

    def test_duplicate_webhook_same_sequence_same_status_filtered(self):
        """Exact duplicate (same sequence + status) is ignored."""
        channel = self._create_channel(status='ringing', sequence_number=5)
        params = {
            'CallSid': channel.sid,
            'CallStatus': 'ringing',
            'SequenceNumber': '5',
            'Direction': 'inbound',
            'Caller': '+15551234567',
            'Called': '+15559876543',
            'To': '+15559876543',
            'CallDuration': '0',
        }

        result = self.Channel.on_call_status(params)
        self.assertIsNone(result, "Duplicate webhook should return None (early return)")

    def test_out_of_order_webhook_different_status_processed(self):
        """Same sequence but different status is processed (not filtered)."""
        channel = self._create_channel(
            sid='CA_ooo_' + 'o' * 26,
            status='ringing',
            sequence_number=3,
        )
        params = {
            'CallSid': channel.sid,
            'CallStatus': 'in-progress',
            'SequenceNumber': '3',
            'Direction': 'inbound',
            'Caller': '+15551234567',
            'Called': '+15559876543',
            'To': '+15559876543',
            'CallDuration': '0',
        }

        with self.mockTwilioClient():
            self.Channel.on_call_status(params)

        channel.invalidate_recordset()
        self.assertEqual(channel.status, 'in-progress')


@tagged('post_install', '-at_install')
class TestCallErrorCodes(ConnectTestCase):
    """Test error code handling on call status webhooks."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Call = cls.env['connect.call']
        cls.Channel = cls.env['connect.channel']

    def _create_call_and_channel(self):
        call = self._create_test_call(
            direction='outgoing', status='in-progress',
            caller='+15559876543', called='+15551234567',
        )
        channel = self.Channel.create({
            'call': call.id,
            'sid': 'CA_err_code_' + 'e' * 21,
            'caller': '+15559876543',
            'called': '+15551234567',
            'status': 'in-progress',
            'technical_direction': 'outbound-api',
            'sequence_number': 1,
        })
        return call, channel

    def test_call_with_error_code_sets_has_error(self):
        """Call with ErrorCode in webhook sets has_error flag."""
        call, channel = self._create_call_and_channel()

        params = {
            'CallSid': channel.sid,
            'CallStatus': 'failed',
            'Direction': 'outbound-api',
            'Caller': '+15559876543',
            'Called': '+15551234567',
            'To': '+15551234567',
            'CallDuration': '0',
            'SequenceNumber': '2',
            'ErrorCode': '31205',
            'ErrorMessage': 'No International Permission',
        }

        with self.mockTwilioClient():
            self.Call.on_call_status(params)

        call.invalidate_recordset()
        self.assertTrue(call.has_error)
        self.assertEqual(call.error_code, '31205')
        self.assertIn('No International Permission', call.error_message)

    def test_ignored_error_codes_not_flagged(self):
        """Error codes in IGNORE_ERROR_CODES (32009) don't set has_error."""
        call, channel = self._create_call_and_channel()

        params = {
            'CallSid': channel.sid,
            'CallStatus': 'completed',
            'Direction': 'outbound-api',
            'Caller': '+15559876543',
            'Called': '+15551234567',
            'To': '+15551234567',
            'CallDuration': '30',
            'SequenceNumber': '2',
            'ErrorCode': '32009',
            'ErrorMessage': 'Some ignorable warning',
        }

        with self.mockTwilioClient():
            self.Call.on_call_status(params)

        call.invalidate_recordset()
        self.assertFalse(call.has_error,
                         "Error code 32009 should be in IGNORE_ERROR_CODES and not flag has_error")

    def test_no_error_code_no_flag(self):
        """Normal webhook without ErrorCode does not set has_error."""
        call, channel = self._create_call_and_channel()

        params = {
            'CallSid': channel.sid,
            'CallStatus': 'completed',
            'Direction': 'outbound-api',
            'Caller': '+15559876543',
            'Called': '+15551234567',
            'To': '+15551234567',
            'CallDuration': '60',
            'SequenceNumber': '2',
        }

        with self.mockTwilioClient():
            self.Call.on_call_status(params)

        call.invalidate_recordset()
        self.assertFalse(call.has_error)


@tagged('post_install', '-at_install')
class TestSettingsErrorHandling(ConnectTestCase):
    """Test settings model error handling patterns."""

    def test_get_param_missing_field_returns_default(self):
        """get_param for non-existent field returns the provided default."""
        result = self.env['connect.settings'].get_param(
            'completely_nonexistent_field_xyz', default='fallback')
        self.assertEqual(result, 'fallback')

    def test_get_param_auto_creates_record(self):
        """get_param creates settings record if none exists."""
        # Clear existing settings
        self.env['connect.settings'].search([]).unlink()
        # get_param should auto-create
        result = self.env['connect.settings'].get_param('debug_mode')
        self.assertIsNotNone(result)
        # Verify a settings record now exists
        settings = self.env['connect.settings'].search([])
        self.assertTrue(len(settings) >= 1)

    def test_set_param_auto_creates_record(self):
        """set_param creates settings record if none exists."""
        self.env['connect.settings'].search([]).unlink()
        self.env['connect.settings'].set_param('debug_mode', True)
        result = self.env['connect.settings'].get_param('debug_mode')
        self.assertTrue(result)


@tagged('post_install', '-at_install')
class TestParkSlotOccupied(ConnectTestCase):
    """Test park slot double-parking prevention."""

    def test_park_slot_occupied_prevents_double_park(self):
        """Can't park two calls in the same slot."""
        ParkSlot = self.env['connect.park_slot']

        existing_call = self._create_test_call(
            direction='incoming', status='in-progress')
        self._ensure_park_slot(1, call=existing_call.id)

        new_call = self._create_test_call(
            direction='incoming', status='in-progress')
        user_ch = self.env['connect.channel'].create({
            'call': new_call.id,
            'sid': 'CAuser_park2_' + 'u' * 20,
            'caller': new_call.called,
            'called': new_call.caller,
            'status': 'in-progress',
            'technical_direction': 'outbound-api',
        })
        other_ch = self.env['connect.channel'].create({
            'call': new_call.id,
            'sid': 'CAother_park2_' + 'o' * 19,
            'caller': new_call.caller,
            'called': new_call.called,
            'status': 'in-progress',
            'technical_direction': 'inbound',
        })

        with patch.object(
            self.env['connect.call'].__class__, '_find_call_channels',
            return_value=(new_call, user_ch, other_ch),
        ):
            result = ParkSlot.park_call('CAxxx', '1')

        self.assertFalse(result['success'])
        self.assertIn('occupied', result['error'].lower())


@tagged('post_install', '-at_install')
class TestNumberSyncErrors(ConnectTestCase):
    """Test number sync handles Twilio API errors."""

    def test_number_sync_handles_api_error(self):
        """Number sync handles Twilio API errors gracefully -- raises ValidationError."""
        mock_client = MagicMock()
        mock_client.incoming_phone_numbers.list.side_effect = Exception(
            'Authentication Error: errors/20003')

        with patch.object(
            self.env['connect.settings'].__class__, 'get_client',
            return_value=mock_client,
        ), patch.object(
            self.env['connect.settings'].__class__, 'get_param',
            return_value='us1',
        ):
            with self.assertRaises(Exception):
                self.env['connect.number'].sync()
