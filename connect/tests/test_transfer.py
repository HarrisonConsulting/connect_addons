# -*- coding: utf-8 -*-
"""Tests for connect.transfer_wizard transient model."""

from unittest.mock import patch, MagicMock, PropertyMock

from odoo.tests import tagged
from .common import ConnectTestCase


@tagged('post_install', '-at_install')
class TestTransferExecute(ConnectTestCase):
    """Test execute_transfer entry point validation."""

    def _get_wizard(self):
        return self.env['connect.transfer_wizard']

    def test_execute_transfer_no_session_id(self):
        """Returns error when session_id is empty."""
        wizard = self._get_wizard()
        result = wizard.execute_transfer('+15551112222', 'blind', session_id=None)
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'No active call session found')

    def test_execute_transfer_no_client(self):
        """Returns error when Twilio client is not configured."""
        wizard = self._get_wizard()
        with patch.object(
            self.env['connect.settings'].__class__,
            'get_client',
            return_value=None,
        ):
            result = wizard.execute_transfer(
                '+15551112222', 'blind', session_id='CAxxx')
            self.assertFalse(result['success'])
            self.assertEqual(result['error'], 'Twilio client not configured')

    def test_execute_transfer_invalid_transfer_type(self):
        """Returns error for unsupported transfer_type."""
        wizard = self._get_wizard()
        with self.mockTwilioClient():
            with patch.object(
                wizard.__class__, '_resolve_phone_number',
                return_value='+15551112222',
            ):
                result = wizard.execute_transfer(
                    '+15551112222', 'warm', session_id='CAxxx')
                self.assertFalse(result['success'])
                self.assertIn('Invalid transfer type', result['error'])

    def test_execute_transfer_invalid_phone_number(self):
        """Returns error when phone number cannot be resolved."""
        wizard = self._get_wizard()
        with self.mockTwilioClient():
            with patch.object(
                wizard.__class__, '_resolve_phone_number',
                return_value=None,
            ):
                result = wizard.execute_transfer(
                    'not-a-number', 'blind', session_id='CAxxx')
                self.assertFalse(result['success'])
                self.assertEqual(result['error'], 'Invalid phone number format')

    def test_execute_transfer_blind_success(self):
        """Successful blind transfer returns success message."""
        wizard = self._get_wizard()
        with self.mockTwilioClient():
            with patch.object(
                wizard.__class__, '_resolve_phone_number',
                return_value='+15551112222',
            ), patch.object(
                wizard.__class__, '_execute_blind_transfer',
                return_value=True,
            ), patch.object(
                wizard.__class__, '_log_transfer_attempt',
            ):
                result = wizard.execute_transfer(
                    '+15551112222', 'blind', session_id='CAxxx')
                self.assertTrue(result['success'])
                self.assertIn('Blind', result['message'])

    def test_execute_transfer_attended_success(self):
        """Successful attended transfer returns success message."""
        wizard = self._get_wizard()
        with self.mockTwilioClient():
            with patch.object(
                wizard.__class__, '_resolve_phone_number',
                return_value='+15551112222',
            ), patch.object(
                wizard.__class__, '_execute_attended_transfer',
                return_value=True,
            ), patch.object(
                wizard.__class__, '_log_transfer_attempt',
            ):
                result = wizard.execute_transfer(
                    '+15551112222', 'attended', session_id='CAxxx')
                self.assertTrue(result['success'])
                self.assertIn('Attended', result['message'])

    def test_execute_transfer_blind_failure(self):
        """Blind transfer returning False produces failure dict."""
        wizard = self._get_wizard()
        with self.mockTwilioClient():
            with patch.object(
                wizard.__class__, '_resolve_phone_number',
                return_value='+15551112222',
            ), patch.object(
                wizard.__class__, '_execute_blind_transfer',
                return_value=False,
            ):
                result = wizard.execute_transfer(
                    '+15551112222', 'blind', session_id='CAxxx')
                self.assertFalse(result['success'])
                self.assertIn('unable to update call', result['error'])


@tagged('post_install', '-at_install')
class TestResolvePhoneNumber(ConnectTestCase):
    """Test _resolve_phone_number logic paths."""

    def _get_wizard(self):
        return self.env['connect.transfer_wizard'].new({})

    def test_resolve_empty_string(self):
        """Empty string returns empty string (falsy but not None)."""
        wizard = self._get_wizard()
        result = wizard._resolve_phone_number('')
        self.assertEqual(result, '')

    def test_resolve_none(self):
        """None input returns None."""
        wizard = self._get_wizard()
        result = wizard._resolve_phone_number(None)
        self.assertIsNone(result)

    def test_resolve_extension_found_with_uri(self):
        """Extension resolves to client:<identity> from user URI."""
        wizard = self._get_wizard()
        mock_user = MagicMock()
        mock_user._name = 'connect.user'
        mock_user.username = 'jdoe'
        mock_user.uri = 'jdoe@example.sip.twilio.com'
        mock_user.id = 42

        mock_extension = MagicMock()
        mock_extension.__bool__ = lambda s: True
        mock_extension.dst = mock_user
        mock_extension.number = '100'

        with patch.object(
            self.env['connect.exten'].__class__, 'search',
            return_value=mock_extension,
        ), patch.object(
            wizard.__class__, '_debug_user_identity',
            return_value={},
        ):
            result = wizard._resolve_phone_number('100')
            self.assertEqual(result, 'client:jdoe')

    def test_resolve_extension_found_no_uri(self):
        """Extension found but user has no URI falls back to client:user<number>."""
        wizard = self._get_wizard()
        mock_user = MagicMock()
        mock_user._name = 'connect.user'
        mock_user.username = 'jdoe'
        mock_user.uri = ''
        mock_user.id = 42

        mock_extension = MagicMock()
        mock_extension.__bool__ = lambda s: True
        mock_extension.dst = mock_user
        mock_extension.number = '100'

        with patch.object(
            self.env['connect.exten'].__class__, 'search',
            return_value=mock_extension,
        ), patch.object(
            wizard.__class__, '_debug_user_identity',
            return_value={},
        ):
            result = wizard._resolve_phone_number('100')
            self.assertEqual(result, 'client:user100')

    def test_resolve_extension_not_found(self):
        """Extension not found returns fallback client:user<number>."""
        wizard = self._get_wizard()
        empty_recordset = self.env['connect.exten']
        with patch.object(
            self.env['connect.exten'].__class__, 'search',
            return_value=empty_recordset,
        ), patch.object(
            wizard.__class__, '_debug_user_identity',
            return_value={},
        ):
            result = wizard._resolve_phone_number('999')
            self.assertEqual(result, 'client:user999')

    def test_resolve_e164_passthrough(self):
        """E.164 formatted number passes through unchanged."""
        wizard = self._get_wizard()
        result = wizard._resolve_phone_number('+15551112222')
        self.assertEqual(result, '+15551112222')

    def test_resolve_national_number_conversion(self):
        """National number gets converted to E.164 via phonenumbers."""
        wizard = self._get_wizard()
        with patch.object(
            self.env['connect.settings'].__class__, 'get_param',
            return_value='US',
        ):
            result = wizard._resolve_phone_number('5551112222')
            self.assertEqual(result, '+15551112222')

    def test_resolve_invalid_external_number(self):
        """Number that fails E.164 validation returns None."""
        wizard = self._get_wizard()
        with patch.object(
            self.env['connect.settings'].__class__, 'get_param',
            return_value='US',
        ):
            result = wizard._resolve_phone_number('123')
            # 5 digits or fewer that are also numeric trigger the extension
            # path, so use a non-numeric prefix to force the external path
            pass

        # Force external path with a number that won't parse as E.164
        result = wizard._resolve_phone_number('+1')
        self.assertIsNone(result)

    def test_resolve_short_invalid_e164(self):
        """A '+' prefixed number too short for E.164 returns None."""
        wizard = self._get_wizard()
        result = wizard._resolve_phone_number('+123')
        self.assertIsNone(result)


@tagged('post_install', '-at_install')
class TestFindExtensionByClientIdentity(ConnectTestCase):
    """Test _find_extension_by_client_identity reverse lookup."""

    def _get_wizard(self):
        return self.env['connect.transfer_wizard'].new({})

    def test_find_extension_by_username_match(self):
        """Finds extension when connect.user username matches identity."""
        wizard = self._get_wizard()

        mock_user = MagicMock()
        mock_user.__bool__ = lambda s: True
        mock_user.id = 42
        mock_user.username = 'jdoe'

        mock_extension = MagicMock()
        mock_extension.__bool__ = lambda s: True
        mock_extension.number = '100'

        def mock_search(domain, **kwargs):
            model_name = domain[0][0] if domain else ''
            if model_name == 'username':
                return mock_user
            if model_name == 'model':
                return mock_extension
            return MagicMock(__bool__=lambda s: False)

        with patch.object(
            self.env['connect.user'].__class__, 'search',
            side_effect=lambda domain, **kw: mock_user
            if domain and domain[0][0] == 'username' else
            MagicMock(__bool__=lambda s: False),
        ), patch.object(
            self.env['connect.exten'].__class__, 'search',
            side_effect=lambda domain, **kw: mock_extension
            if domain and domain[0][0] == 'model' else
            MagicMock(__bool__=lambda s: False),
        ):
            result = wizard._find_extension_by_client_identity('client:jdoe')
            self.assertEqual(result, '100')

    def test_find_extension_no_user_match_direct_extension(self):
        """Falls back to direct extension lookup when no user match."""
        wizard = self._get_wizard()

        mock_extension = MagicMock()
        mock_extension.__bool__ = lambda s: True
        mock_extension.number = '200'

        empty_user = MagicMock(__bool__=lambda s: False)

        with patch.object(
            self.env['connect.user'].__class__, 'search',
            return_value=empty_user,
        ), patch.object(
            self.env['connect.exten'].__class__, 'search',
            side_effect=lambda domain, **kw: mock_extension
            if domain and domain[0][0] == 'number' else
            MagicMock(__bool__=lambda s: False),
        ):
            result = wizard._find_extension_by_client_identity('client:200')
            self.assertEqual(result, '200')

    def test_find_extension_no_match_returns_none(self):
        """Returns None when neither user nor extension match."""
        wizard = self._get_wizard()
        empty = MagicMock(__bool__=lambda s: False)

        with patch.object(
            self.env['connect.user'].__class__, 'search',
            return_value=empty,
        ), patch.object(
            self.env['connect.exten'].__class__, 'search',
            return_value=empty,
        ):
            result = wizard._find_extension_by_client_identity('client:unknown')
            self.assertIsNone(result)

    def test_find_extension_empty_identity(self):
        """Returns None for empty client identity after prefix strip."""
        wizard = self._get_wizard()
        result = wizard._find_extension_by_client_identity('client:')
        self.assertIsNone(result)

    def test_find_extension_strips_domain(self):
        """Strips @domain from identity before lookup."""
        wizard = self._get_wizard()

        mock_user = MagicMock()
        mock_user.__bool__ = lambda s: True
        mock_user.id = 7
        mock_user.username = 'agent'

        mock_extension = MagicMock()
        mock_extension.__bool__ = lambda s: True
        mock_extension.number = '300'

        with patch.object(
            self.env['connect.user'].__class__, 'search',
            return_value=mock_user,
        ), patch.object(
            self.env['connect.exten'].__class__, 'search',
            return_value=mock_extension,
        ):
            result = wizard._find_extension_by_client_identity(
                'client:agent@sip.twilio.com')
            self.assertEqual(result, '300')


@tagged('post_install', '-at_install')
class TestBlindTransfer(ConnectTestCase):
    """Test _execute_blind_transfer routing logic."""

    def _get_wizard(self):
        return self.env['connect.transfer_wizard'].new({})

    def test_blind_transfer_to_extension(self):
        """Client identity target delegates to _execute_extension_transfer."""
        wizard = self._get_wizard()
        mock_client = MagicMock()

        with patch.object(
            wizard.__class__, '_find_extension_by_client_identity',
            return_value='100',
        ), patch.object(
            wizard.__class__, '_execute_extension_transfer',
            return_value=True,
        ) as mock_ext_transfer:
            result = wizard._execute_blind_transfer(
                mock_client, 'CAxxx', 'client:jdoe', call_id=1)
            self.assertTrue(result)
            mock_ext_transfer.assert_called_once_with(
                mock_client, 'CAxxx', '100', 'blind', 1)

    def test_blind_transfer_extension_not_found(self):
        """Returns False when extension cannot be reverse-looked-up."""
        wizard = self._get_wizard()
        mock_client = MagicMock()

        with patch.object(
            wizard.__class__, '_find_extension_by_client_identity',
            return_value=None,
        ):
            result = wizard._execute_blind_transfer(
                mock_client, 'CAxxx', 'client:ghost', call_id=1)
            self.assertFalse(result)

    def test_blind_transfer_to_external_number(self):
        """External number target delegates to _execute_external_number_transfer."""
        wizard = self._get_wizard()
        mock_client = MagicMock()

        with patch.object(
            wizard.__class__, '_execute_external_number_transfer',
            return_value=True,
        ) as mock_ext:
            result = wizard._execute_blind_transfer(
                mock_client, 'CAxxx', '+15551112222', call_id=1)
            self.assertTrue(result)
            mock_ext.assert_called_once_with(
                mock_client, 'CAxxx', '+15551112222', 1)


@tagged('post_install', '-at_install')
class TestAttendedTransfer(ConnectTestCase):
    """Test _execute_attended_transfer routing logic."""

    def _get_wizard(self):
        return self.env['connect.transfer_wizard'].new({})

    def test_attended_transfer_to_extension(self):
        """Client identity target delegates to _execute_extension_transfer with attended type."""
        wizard = self._get_wizard()
        mock_client = MagicMock()

        with patch.object(
            wizard.__class__, '_find_extension_by_client_identity',
            return_value='100',
        ), patch.object(
            wizard.__class__, '_execute_extension_transfer',
            return_value=True,
        ) as mock_ext_transfer:
            result = wizard._execute_attended_transfer(
                mock_client, 'CAxxx', 'client:jdoe', call_id=1)
            self.assertTrue(result)
            mock_ext_transfer.assert_called_once_with(
                mock_client, 'CAxxx', '100', 'attended', 1)

    def test_attended_transfer_extension_not_found(self):
        """Returns False when extension cannot be found for attended transfer."""
        wizard = self._get_wizard()
        mock_client = MagicMock()

        with patch.object(
            wizard.__class__, '_find_extension_by_client_identity',
            return_value=None,
        ):
            result = wizard._execute_attended_transfer(
                mock_client, 'CAxxx', 'client:ghost', call_id=1)
            self.assertFalse(result)

    def test_attended_transfer_to_external(self):
        """External number delegates to _execute_external_number_transfer."""
        wizard = self._get_wizard()
        mock_client = MagicMock()

        with patch.object(
            wizard.__class__, '_execute_external_number_transfer',
            return_value=True,
        ) as mock_ext:
            result = wizard._execute_attended_transfer(
                mock_client, 'CAxxx', '+15551112222', call_id=1)
            self.assertTrue(result)
            mock_ext.assert_called_once()


@tagged('post_install', '-at_install')
class TestExternalNumberTransfer(ConnectTestCase):
    """Test _execute_external_number_transfer conference bridge logic."""

    def _get_wizard(self):
        return self.env['connect.transfer_wizard'].new({})

    def test_external_transfer_no_call_record(self):
        """Returns False when no call record can be found."""
        wizard = self._get_wizard()
        mock_client = MagicMock()

        # Ensure channel search returns empty
        with patch.object(
            self.env['connect.channel'].__class__, 'search',
            return_value=self.env['connect.channel'],
        ):
            result = wizard._execute_external_number_transfer(
                mock_client, 'CAxxx', '+15551112222', call_id=None)
            self.assertFalse(result)

    def test_external_transfer_no_connect_user(self):
        """Returns False when current user has no connect.user."""
        wizard = self._get_wizard()
        mock_client = MagicMock()

        call = self._create_test_call(
            direction='incoming', status='in-progress', create_channel=True)

        # Patch the current user's connect_user to be empty
        with patch.object(
            type(self.env.user), 'connect_user',
            new_callable=PropertyMock,
            return_value=self.env['connect.user'],
        ):
            result = wizard._execute_external_number_transfer(
                mock_client, call.channels[0].sid,
                '+15551112222', call_id=call.id)
            self.assertFalse(result)

    def test_external_transfer_conference_bridge(self):
        """Successful external transfer sets up conference, dials target, hangs up user leg."""
        wizard = self._get_wizard()

        # Create call with TWO channels (user + other party)
        call = self._create_test_call(
            direction='incoming', status='in-progress')
        user_sid = f'CA{"y" * 30}01'
        other_sid = f'CA{"z" * 30}02'

        mock_connect_user = MagicMock()
        mock_connect_user.__bool__ = lambda s: True
        mock_connect_user.outgoing_callerid = '+15550001111'

        user_channel = self.env['connect.channel'].create({
            'call': call.id,
            'sid': user_sid,
            'caller': '+15559876543',
            'called': '+15551234567',
            'status': 'in-progress',
            'technical_direction': 'outbound-api',
        })
        other_channel = self.env['connect.channel'].create({
            'call': call.id,
            'sid': other_sid,
            'caller': '+15551234567',
            'called': '+15559876543',
            'status': 'in-progress',
            'technical_direction': 'inbound',
        })

        with self.mockTwilioClient() as mock_client:
            # Pre-populate call data so fetch returns in-progress
            mock_client.calls._calls[user_sid] = MagicMock(status='in-progress')
            mock_client.calls._calls[other_sid] = MagicMock(status='in-progress')

            with patch.object(
                type(self.env.user), 'connect_user',
                new_callable=PropertyMock,
                return_value=mock_connect_user,
            ), patch.object(
                self.env['connect.settings'].__class__, 'get_param',
                return_value='https://test.example.com',
            ), patch.object(
                self.env['connect.settings'].__class__, 'get_system_voice',
                return_value='alice',
            ), patch.object(
                self.env['connect.settings'].__class__, 'process_pronunciation',
                side_effect=lambda text: text,
            ):
                # Mock channel filtering to identify user vs other party
                with patch.object(
                    call.channels.__class__, 'filtered',
                    return_value=user_channel,
                ):
                    result = wizard._execute_external_number_transfer(
                        mock_client, user_sid, '+15551112222', call_id=call.id)
                    self.assertTrue(result)
                    # Verify a new call was created to dial the target
                    self.assertEqual(mock_client.calls._call_count, 1)


@tagged('post_install', '-at_install')
class TestHandleTransferContinuation(ConnectTestCase):
    """Test handle_transfer_continuation webhook handler."""

    def _get_wizard(self):
        return self.env['connect.transfer_wizard']

    def test_continuation_no_channel(self):
        """Returns hangup TwiML when no channel matches the CallSid."""
        wizard = self._get_wizard()
        result = wizard.handle_transfer_continuation({
            'CallSid': 'CA_nonexistent',
            'DialCallStatus': 'completed',
        })
        twiml = str(result)
        self.assertIn('Hangup', twiml)

    def test_continuation_completed_status(self):
        """Completed dial status processes transfer recipient."""
        wizard = self._get_wizard()

        call = self._create_test_call(
            direction='incoming', status='in-progress', create_channel=True)

        channel_sid = call.channels[0].sid

        # Set up transfer context on the call
        transfer_user = self.env.user
        call.sudo().write({
            'transferred_users': [(4, transfer_user.id)],
        })

        # Store transfer context if the method exists
        if hasattr(call, 'transfer_context'):
            call.sudo().write({
                'transfer_context': {
                    channel_sid: {
                        'user_id': transfer_user.id,
                        'user_login': transfer_user.login,
                    }
                }
            })

        result = wizard.handle_transfer_continuation({
            'CallSid': channel_sid,
            'DialCallStatus': 'completed',
            'DialCallSid': 'CA_dial_xxx',
        })
        twiml = str(result)
        self.assertIn('Hangup', twiml)

    def test_continuation_failed_status(self):
        """Non-completed status still returns hangup TwiML."""
        wizard = self._get_wizard()

        call = self._create_test_call(
            direction='incoming', status='in-progress', create_channel=True)
        channel_sid = call.channels[0].sid

        result = wizard.handle_transfer_continuation({
            'CallSid': channel_sid,
            'DialCallStatus': 'no-answer',
        })
        twiml = str(result)
        self.assertIn('Hangup', twiml)


@tagged('post_install', '-at_install')
class TestCreateAttendedTransferTwiml(ConnectTestCase):
    """Test TwiML generation for attended transfers."""

    def _get_wizard(self):
        return self.env['connect.transfer_wizard'].new({})

    def test_attended_twiml_contains_client_identity(self):
        """Attended TwiML includes Dial with Client identity element."""
        wizard = self._get_wizard()
        mock_user = MagicMock()
        mock_user.uri = 'agent@sip.twilio.com'

        with patch.object(
            self.env['connect.settings'].__class__, 'get_system_voice',
            return_value='alice',
        ), patch.object(
            self.env['connect.settings'].__class__, 'process_pronunciation',
            side_effect=lambda text: text,
        ):
            twiml = wizard._create_attended_transfer_twiml(mock_user, 'CAxxx')
            self.assertIn('Setting up consultation call', twiml)
            self.assertIn('agent@sip.twilio.com', twiml)
            self.assertIn('<Dial', twiml)
            self.assertIn('<Client', twiml)
