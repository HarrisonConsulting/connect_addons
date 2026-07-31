# -*- coding: utf-8 -*-
"""Tests for connect.user model: SIP management, presence, rendering, and token generation."""

from unittest.mock import patch, MagicMock

from psycopg2.errors import SerializationFailure
from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import tagged
from .common import ConnectTestCase


@tagged('post_install', '-at_install')
class TestUserSipAccount(ConnectTestCase):
    """Test SIP account creation and credential management."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twiml_app = cls.env['connect.twiml'].create({
            'name': 'User SIP App',
            'code_type': 'model_method',
            'model': 'connect.domain',
            'method': 'route_call',
        })
        cls.domain = cls.env['connect.domain'].with_context(
            no_twilio_create=True,
        ).create({
            'friendly_name': 'SIP Test Domain',
            'subdomain': 'siptest',
            'application': cls.twiml_app.id,
            'sid': 'SD_sip_test',
            'cred_list_sid': 'CL_sip_test',
        })

    def test_create_sip_account(self):
        """Creating SIP account generates credentials in Twilio."""
        mock_client = MagicMock()
        mock_credential = MagicMock(sid='CR_new_credential')
        mock_client.sip.credential_lists.return_value.credentials.create.return_value = mock_credential

        with patch.object(
            self.env['connect.settings'].__class__, 'get_client',
            return_value=mock_client,
        ), patch.object(
            self.env['connect.settings'].__class__, 'get_param',
            return_value=True,
        ):
            user = self.env['connect.user'].with_context(
                no_clear_cache=True,
            ).create({
                'username': 'sipuser1',
                'domain': self.domain.id,
                'sip_enabled': True,
                'password': 'Str0ngPa$$w0rd',
            })
            self.assertEqual(user.sid, 'CR_new_credential')
            # Password should be masked
            self.assertNotIn('Str0ng', user.password)
            self.assertTrue(all(c == '*' for c in user.password))

    def test_sip_password_strength_validation(self):
        """Weak SIP passwords are rejected (12+ chars, mixed case, digit)."""
        mock_client = MagicMock()
        mock_client.sip.credential_lists.return_value.credentials.create.side_effect = \
            Exception('A strong password is required')

        with patch.object(
            self.env['connect.settings'].__class__, 'get_client',
            return_value=mock_client,
        ), patch.object(
            self.env['connect.settings'].__class__, 'get_param',
            return_value=True,
        ):
            with self.assertRaises(ValidationError) as ctx:
                self.env['connect.user'].with_context(
                    no_clear_cache=True,
                ).create({
                    'username': 'weakpwd',
                    'domain': self.domain.id,
                    'sip_enabled': True,
                    'password': 'short',
                })
            self.assertIn('strong password', str(ctx.exception))

    def test_create_sip_account_already_exists(self):
        """Creating SIP account that already exists imports the existing SID."""
        mock_client = MagicMock()
        # First call: "already exists" error
        mock_client.sip.credential_lists.return_value.credentials.create.side_effect = \
            Exception('Credential already exists')

        # List returns the matching credential
        mock_existing_cred = MagicMock(sid='CR_existing', username='existinguser')
        mock_client.sip.credential_lists.return_value.credentials.list.return_value = [
            mock_existing_cred,
        ]

        with patch.object(
            self.env['connect.settings'].__class__, 'get_client',
            return_value=mock_client,
        ), patch.object(
            self.env['connect.settings'].__class__, 'get_param',
            return_value=True,
        ):
            user = self.env['connect.user'].with_context(
                no_clear_cache=True,
            ).create({
                'username': 'existinguser',
                'domain': self.domain.id,
                'sip_enabled': True,
                'password': 'Str0ngPa$$w0rd',
            })
            self.assertEqual(user.sid, 'CR_existing')

    def test_username_must_be_alphanumeric(self):
        """Username with non-alphanumeric characters raises ValidationError."""
        with self.assertRaises(ValidationError) as ctx:
            self.env['connect.user'].with_context(
                no_twilio_create=True,
                no_clear_cache=True,
            ).create({
                'username': 'bad-user!',
                'domain': self.domain.id,
            })
        self.assertIn('alphanumeric', str(ctx.exception))

    def test_username_cannot_be_changed(self):
        """Writing to username field raises ValidationError."""
        user = self.env['connect.user'].with_context(
            no_twilio_create=True,
            no_clear_cache=True,
        ).create({
            'username': 'norenameme',
            'domain': self.domain.id,
        })
        with self.assertRaises(ValidationError) as ctx:
            user.write({'username': 'newname'})
        self.assertIn('cannot be changed', str(ctx.exception))


@tagged('post_install', '-at_install')
class TestUserSipUri(ConnectTestCase):
    """Test SIP URI computed fields."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twiml_app = cls.env['connect.twiml'].create({
            'name': 'URI App',
            'code_type': 'model_method',
            'model': 'connect.domain',
            'method': 'route_call',
        })
        cls.domain = cls.env['connect.domain'].with_context(
            no_twilio_create=True,
        ).create({
            'friendly_name': 'URI Domain',
            'subdomain': 'uritest',
            'application': cls.twiml_app.id,
        })
        cls.user = cls.env['connect.user'].with_context(
            no_twilio_create=True,
            no_clear_cache=True,
        ).create({
            'username': 'jdoe',
            'domain': cls.domain.id,
            'twilio_edge': 'roaming',
        })

    def test_sip_uri_computation(self):
        """SIP URI computed correctly from username and domain."""
        self.assertEqual(self.user.uri, 'jdoe@uritest.sip.twilio.com')

    def test_sip_uri_roaming_edge(self):
        """Roaming edge uses global domain (no edge prefix)."""
        self.user.with_context(skip_sync=True).write({'twilio_edge': 'roaming'})
        self.user.invalidate_recordset(['connect_uri'])
        self.assertEqual(self.user.connect_uri, 'jdoe@uritest.sip.twilio.com')

    def test_sip_uri_specific_edge(self):
        """Specific edge includes edge in connect_uri."""
        self.user.with_context(skip_sync=True).write({'twilio_edge': 'ashburn'})
        self.user.invalidate_recordset(['connect_uri'])
        self.assertEqual(
            self.user.connect_uri, 'jdoe@uritest.sip.ashburn.twilio.com')


@tagged('post_install', '-at_install')
class TestGetClientToken(ConnectTestCase):
    """Test JWT token generation for WebRTC client."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twiml_app = cls.env['connect.twiml'].create({
            'name': 'Token App',
            'code_type': 'model_method',
            'model': 'connect.domain',
            'method': 'route_call',
            'sid': 'AP_token_app',
        })
        cls.domain = cls.env['connect.domain'].with_context(
            no_twilio_create=True,
        ).create({
            'friendly_name': 'Token Domain',
            'subdomain': 'tokentest',
            'application': cls.twiml_app.id,
        })
        cls.connect_user = cls.env['connect.user'].with_context(
            no_twilio_create=True,
            no_clear_cache=True,
        ).create({
            'username': 'tokenuser',
            'domain': cls.domain.id,
            'client_enabled': True,
            'user': cls.env.user.id,
        })
        # Token issuance is gated on connect group membership
        # (_can_issue_client_token). Odoo 19 has_group() has no superuser
        # bypass, so the test user must genuinely be in the group.
        cls.env.user.write({'group_ids': [Command.link(
            cls.env.ref('connect.group_connect_user').id)]})

    def test_get_client_token(self):
        """JWT token generated with correct identity and grants."""
        with patch.object(
            self.env['connect.settings'].__class__, 'get_param',
            side_effect=lambda param, *a, **kw: {
                'account_sid': 'ACtest123',
                'twilio_api_key': 'SKtest_api_key',
                'twilio_api_secret': 'test_secret_value',
                'twilio_region': 'us1',
                'twilio_edge': 'ashburn',
            }.get(param, ''),
        ):
            result = self.env['connect.user'].get_client_token()
            self.assertIn('token', result)
            self.assertTrue(result['token'])
            self.assertNotIn('error', result)
            # Token should be a JWT string (header.payload.signature)
            token_str = result['token']
            if isinstance(token_str, bytes):
                token_str = token_str.decode('utf-8')
            self.assertEqual(token_str.count('.'), 2)

    def test_get_client_token_disabled_client(self):
        """User with client_enabled=False gets no token."""
        self.connect_user.with_context(skip_sync=True).write({
            'client_enabled': False,
        })
        result = self.env['connect.user'].get_client_token()
        self.assertFalse(result.get('token'))

        # Restore
        self.connect_user.with_context(skip_sync=True).write({
            'client_enabled': True,
        })

    def test_get_client_token_missing_credentials(self):
        """No API key credentials (neutralized/fresh copy): clean False.

        Must not reach AccessToken/to_jwt — that raises 'JWT does not have
        a signing key configured' and used to surface as an ERROR traceback
        on every neutralized copy.
        """
        with patch.object(
            self.env['connect.settings'].__class__, 'get_param',
            side_effect=lambda param, *a, **kw: '',
        ):
            result = self.env['connect.user'].get_client_token()
        self.assertFalse(result.get('token'))
        self.assertNotIn('error', result)

    def test_get_client_token_no_connect_user(self):
        """User without connect.user record gets no token."""
        # Temporarily unlink connect_user from res.users
        self.connect_user.with_context(skip_sync=True).write({
            'user': False,
        })
        result = self.env['connect.user'].get_client_token()
        self.assertFalse(result.get('token'))

        # Restore
        self.connect_user.with_context(skip_sync=True).write({
            'user': self.env.user.id,
        })


@tagged('post_install', '-at_install')
class TestUserRender(ConnectTestCase):
    """Test connect.user render methods for TwiML generation."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twiml_app = cls.env['connect.twiml'].create({
            'name': 'Render App',
            'code_type': 'model_method',
            'model': 'connect.domain',
            'method': 'route_call',
        })
        cls.domain = cls.env['connect.domain'].with_context(
            no_twilio_create=True,
        ).create({
            'friendly_name': 'Render Domain',
            'subdomain': 'rendertest',
            'application': cls.twiml_app.id,
        })

    def _create_user(self, **kwargs):
        defaults = {
            'domain': self.domain.id,
            'client_enabled': True,
            'sip_enabled': False,
            'record_calls': False,
            'client_ring_timeout': 20,
            'sip_ring_timeout': 30,
        }
        defaults.update(kwargs)
        return self.env['connect.user'].with_context(
            no_twilio_create=True,
            no_clear_cache=True,
        ).create(defaults)

    def test_render_client_twiml(self):
        """Client TwiML renders with correct timeout in Dial."""
        user = self._create_user(username='clientrender', client_ring_timeout=25)
        response = MagicMock()
        request = {
            'Caller': 'sip:someone@rendertest.sip.twilio.com',
            'CallSid': 'CA_render_001',
        }
        with patch.object(
            self.env['connect.settings'].__class__, 'get_param',
            side_effect=lambda param, *a, **kw: {
                'api_url': 'https://test.example.com/',
                'twilio_edge': 'ashburn',
            }.get(param, ''),
        ), patch.object(
            self.env['connect.channel'].__class__, 'search',
            return_value=self.env['connect.channel'],
        ):
            # render_client appends to response object
            from twilio.twiml.voice_response import VoiceResponse
            real_response = VoiceResponse()
            user.render_client(real_response, request, {})
            twiml = str(real_response)
            self.assertIn('<Dial', twiml)
            self.assertIn('<Client', twiml)
            self.assertIn('timeout="25"', twiml)

    def test_render_sip_twiml(self):
        """SIP TwiML renders with correct URI."""
        user = self._create_user(
            username='siprender',
            sip_enabled=True,
            sip_ring_timeout=35,
        )
        request = {
            'Caller': 'sip:someone@rendertest.sip.twilio.com',
            'CallSid': 'CA_render_002',
        }
        with patch.object(
            self.env['connect.settings'].__class__, 'get_param',
            side_effect=lambda param, *a, **kw: {
                'api_url': 'https://test.example.com/',
                'twilio_edge': 'ashburn',
            }.get(param, ''),
        ), patch.object(
            self.env['connect.channel'].__class__, 'search',
            return_value=self.env['connect.channel'],
        ):
            from twilio.twiml.voice_response import VoiceResponse
            real_response = VoiceResponse()
            user.render_sip(real_response, request, {})
            twiml = str(real_response)
            self.assertIn('<Dial', twiml)
            self.assertIn('<Sip', twiml)
            self.assertIn('siprender@rendertest.sip.twilio.com', twiml)
            self.assertIn('timeout="35"', twiml)

    def test_dnd_routes_to_voicemail(self):
        """User with DND enabled routes calls to voicemail."""
        voicemail_audio = self.env['connect.audio'].create({
            'name': 'DND voicemail',
            'source': 'twilio_tts',
            'static_text': 'Leave a message for DND user.',
        })
        user = self._create_user(
            username='dnduser',
            dnd_enabled=True,
            voicemail_enabled=True,
            voicemail_audio_id=voicemail_audio.id,
        )
        with patch.object(
            self.env['connect.settings'].__class__, 'get_param',
            side_effect=lambda param, *a, **kw: {
                'api_url': 'https://test.example.com/',
                'twilio_edge': 'ashburn',
                'voicemail_max_length': '120',
                'voicemail_finish_key': '#',
            }.get(param, ''),
        ), patch.object(
            self.env['connect.settings'].__class__, 'get_system_voice',
            return_value='alice',
        ), patch.object(
            self.env['connect.settings'].__class__, 'process_pronunciation',
            side_effect=lambda text: text,
        ):
            result = user.render(request={}, params={})
            self.assertIn('<Record', result)
            self.assertIn('Leave a message for DND user', result)
            self.assertNotIn('<Dial', result)

    def test_dnd_without_voicemail_hangup(self):
        """User with DND but no voicemail hangs up with system message."""
        user = self._create_user(
            username='dndnovm',
            dnd_enabled=True,
            voicemail_enabled=False,
        )
        with patch.object(
            user.__class__, 'tts_system_message',
        ) as mock_tts:
            result = user.render(request={}, params={})
            mock_tts.assert_called_once()
            self.assertIn('Hangup', result)


@tagged('post_install', '-at_install')
class TestUserPresence(ConnectTestCase):
    """Test presence status updates."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twiml_app = cls.env['connect.twiml'].create({
            'name': 'Presence App',
            'code_type': 'model_method',
            'model': 'connect.domain',
            'method': 'route_call',
        })
        cls.domain = cls.env['connect.domain'].with_context(
            no_twilio_create=True,
        ).create({
            'friendly_name': 'Presence Domain',
            'subdomain': 'presencetest',
            'application': cls.twiml_app.id,
        })
        cls.connect_user = cls.env['connect.user'].with_context(
            no_twilio_create=True,
            no_clear_cache=True,
        ).create({
            'username': 'presenceuser',
            'domain': cls.domain.id,
            'user': cls.env.user.id,
        })

    def test_update_presence_available(self):
        """Presence updates to available."""
        with patch.object(
            self.env['bus.bus'].__class__, '_sendone',
        ):
            self.env['connect.user'].update_presence('available')
            self.connect_user.invalidate_recordset(['presence_status'])
            self.assertEqual(self.connect_user.presence_status, 'available')

    def test_update_presence_on_call(self):
        """Presence updates to on_call."""
        with patch.object(
            self.env['bus.bus'].__class__, '_sendone',
        ):
            self.env['connect.user'].update_presence('on_call')
            self.connect_user.invalidate_recordset(['presence_status'])
            self.assertEqual(self.connect_user.presence_status, 'on_call')

    def test_update_presence_on_hold(self):
        """Presence updates to on_hold."""
        with patch.object(
            self.env['bus.bus'].__class__, '_sendone',
        ):
            self.env['connect.user'].update_presence('on_hold')
            self.connect_user.invalidate_recordset(['presence_status'])
            self.assertEqual(self.connect_user.presence_status, 'on_hold')

    def test_update_presence_offline(self):
        """Presence updates to offline."""
        with patch.object(
            self.env['bus.bus'].__class__, '_sendone',
        ):
            self.env['connect.user'].update_presence('offline')
            self.connect_user.invalidate_recordset(['presence_status'])
            self.assertEqual(self.connect_user.presence_status, 'offline')

    def test_update_presence_sets_timestamp(self):
        """Presence update sets presence_updated timestamp."""
        with patch.object(
            self.env['bus.bus'].__class__, '_sendone',
        ):
            self.env['connect.user'].update_presence('available')
            self.connect_user.invalidate_recordset(['presence_updated'])
            self.assertTrue(self.connect_user.presence_updated)

    def test_update_presence_does_not_swallow_serialization_conflict(self):
        """update_presence must not catch SerializationFailure itself.

        write() only marks the field dirty; the actual UPDATE (and any
        SerializationFailure) fires later at flush time, outside any try
        block this method could wrap. A local retry-and-swallow here is
        therefore dead code (it never once fired in 15 days of production
        logs) and, worse, can leave a poisoned transaction behind for the
        next statement to trip over. The real rescue is Odoo's HTTP
        dispatcher retrying the whole request, which requires the error to
        propagate uncaught.
        """
        with patch.object(self.env['bus.bus'].__class__, '_sendone'), \
                patch.object(type(self.connect_user), 'write', autospec=True,
                             side_effect=SerializationFailure()):
            with self.assertRaises(SerializationFailure):
                self.env['connect.user'].update_presence('available')

    def test_get_all_presence(self):
        """get_all_presence returns list with expected fields."""
        result = self.env['connect.user'].get_all_presence()
        self.assertIsInstance(result, list)
        self.assertTrue(len(result) > 0)
        entry = result[0]
        self.assertIn('id', entry)
        self.assertIn('name', entry)
        self.assertIn('presence_status', entry)
        self.assertIn('user_id', entry)


@tagged('post_install', '-at_install')
class TestUserWithoutDomain(ConnectTestCase):
    """Test user behavior edge cases."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twiml_app = cls.env['connect.twiml'].create({
            'name': 'Edge App',
            'code_type': 'model_method',
            'model': 'connect.domain',
            'method': 'route_call',
        })
        cls.domain = cls.env['connect.domain'].with_context(
            no_twilio_create=True,
        ).create({
            'friendly_name': 'Edge Domain',
            'subdomain': 'edgetest',
            'application': cls.twiml_app.id,
        })

    def test_user_client_only(self):
        """User with only client phone enabled (no SIP) can be created."""
        user = self.env['connect.user'].with_context(
            no_twilio_create=True,
            no_clear_cache=True,
        ).create({
            'username': 'clientonly',
            'domain': self.domain.id,
            'client_enabled': True,
            'sip_enabled': False,
        })
        self.assertTrue(user.client_enabled)
        self.assertFalse(user.sip_enabled)
        self.assertFalse(user.sid)
        # URI should still be computed
        self.assertIn('clientonly@edgetest.sip.twilio.com', user.uri)

    def test_user_get_by_uri_sip(self):
        """get_user_by_uri resolves SIP URI to user."""
        user = self.env['connect.user'].with_context(
            no_twilio_create=True,
            no_clear_cache=True,
        ).create({
            'username': 'urilookup',
            'domain': self.domain.id,
        })
        found = self.env['connect.user'].get_user_by_uri(
            'sip:urilookup@edgetest.sip.twilio.com')
        self.assertEqual(found.id, user.id)

    def test_user_get_by_uri_client(self):
        """get_user_by_uri resolves client URI to user."""
        user = self.env['connect.user'].with_context(
            no_twilio_create=True,
            no_clear_cache=True,
        ).create({
            'username': 'clientlookup',
            'domain': self.domain.id,
        })
        found = self.env['connect.user'].get_user_by_uri(
            'client:clientlookup@edgetest.sip.twilio.com')
        self.assertEqual(found.id, user.id)

    def test_user_get_by_uri_empty(self):
        """get_user_by_uri with empty string returns empty recordset."""
        found = self.env['connect.user'].get_user_by_uri('')
        self.assertFalse(found)

    def test_user_get_by_uri_none(self):
        """get_user_by_uri with None returns empty recordset."""
        found = self.env['connect.user'].get_user_by_uri(None)
        self.assertFalse(found)


@tagged('post_install', '-at_install')
class TestGenerateTwilioPassword(ConnectTestCase):
    """Test static password generation method."""

    def test_password_length(self):
        """Generated password is at least 12 characters."""
        for _ in range(10):
            pwd = self.env['connect.user'].generate_twilio_password()
            self.assertGreaterEqual(len(pwd), 12)

    def test_password_has_lowercase(self):
        """Generated password contains at least one lowercase letter."""
        for _ in range(10):
            pwd = self.env['connect.user'].generate_twilio_password()
            self.assertTrue(any(c.islower() for c in pwd))

    def test_password_has_uppercase(self):
        """Generated password contains at least one uppercase letter."""
        for _ in range(10):
            pwd = self.env['connect.user'].generate_twilio_password()
            self.assertTrue(any(c.isupper() for c in pwd))

    def test_password_has_digit(self):
        """Generated password contains at least one digit."""
        for _ in range(10):
            pwd = self.env['connect.user'].generate_twilio_password()
            self.assertTrue(any(c.isdigit() for c in pwd))

    def test_password_uniqueness(self):
        """Sequential calls produce different passwords (statistical)."""
        passwords = {self.env['connect.user'].generate_twilio_password()
                     for _ in range(20)}
        # With 12+ random chars, collisions in 20 tries are essentially impossible
        self.assertGreater(len(passwords), 15)


@tagged('post_install', '-at_install')
class TestUserCallflowManagement(ConnectTestCase):
    """Test automatic callflow creation/deletion on enable/disable."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twiml_app = cls.env['connect.twiml'].create({
            'name': 'Callflow Mgmt App',
            'code_type': 'model_method',
            'model': 'connect.domain',
            'method': 'route_call',
        })
        cls.domain = cls.env['connect.domain'].with_context(
            no_twilio_create=True,
        ).create({
            'friendly_name': 'Callflow Mgmt Domain',
            'subdomain': 'cfmgmt',
            'application': cls.twiml_app.id,
        })

    def test_client_enabled_creates_callflow(self):
        """Enabling client creates user_callflow with render_client method."""
        user = self.env['connect.user'].with_context(
            no_twilio_create=True,
            no_clear_cache=True,
        ).create({
            'username': 'cfclient',
            'domain': self.domain.id,
            'client_enabled': True,
            'sip_enabled': False,
        })
        callflow = self.env['connect.user_callflow'].search([
            ('user', '=', user.id),
            ('callflow_type', '=', 'client'),
        ])
        self.assertTrue(callflow)
        self.assertEqual(callflow.method, 'render_client')

    def test_sip_enabled_creates_callflow(self):
        """Enabling SIP creates user_callflow with render_sip method."""
        user = self.env['connect.user'].with_context(
            no_twilio_create=True,
            no_clear_cache=True,
        ).create({
            'username': 'cfsip',
            'domain': self.domain.id,
            'client_enabled': False,
            'sip_enabled': True,
        })
        callflow = self.env['connect.user_callflow'].search([
            ('user', '=', user.id),
            ('callflow_type', '=', 'sip'),
        ])
        self.assertTrue(callflow)
        self.assertEqual(callflow.method, 'render_sip')

    def test_voicemail_enabled_creates_callflow(self):
        """Enabling voicemail creates user_callflow with render_voicemail method."""
        user = self.env['connect.user'].with_context(
            no_twilio_create=True,
            no_clear_cache=True,
        ).create({
            'username': 'cfvm',
            'domain': self.domain.id,
            'voicemail_enabled': True,
        })
        callflow = self.env['connect.user_callflow'].search([
            ('user', '=', user.id),
            ('callflow_type', '=', 'voicemail'),
        ])
        self.assertTrue(callflow)
        self.assertEqual(callflow.method, 'render_voicemail')
        self.assertEqual(callflow.prio, 10)

    def test_disabling_client_removes_callflow(self):
        """Disabling client removes the client user_callflow."""
        user = self.env['connect.user'].with_context(
            no_twilio_create=True,
            no_clear_cache=True,
        ).create({
            'username': 'cfdisable',
            'domain': self.domain.id,
            'client_enabled': True,
        })
        # Verify callflow exists
        self.assertTrue(self.env['connect.user_callflow'].search([
            ('user', '=', user.id),
            ('callflow_type', '=', 'client'),
        ]))
        # Disable
        user.with_context(skip_sync=True).write({'client_enabled': False})
        self.assertFalse(self.env['connect.user_callflow'].search([
            ('user', '=', user.id),
            ('callflow_type', '=', 'client'),
        ]))

    def test_priority_ordering(self):
        """Client priority 1 + SIP priority 2 results in correct callflow order."""
        user = self.env['connect.user'].with_context(
            no_twilio_create=True,
            no_clear_cache=True,
        ).create({
            'username': 'cfprio',
            'domain': self.domain.id,
            'client_enabled': True,
            'client_priority': '1',
            'sip_enabled': True,
            'sip_priority': '2',
        })
        callflows = self.env['connect.user_callflow'].search(
            [('user', '=', user.id)], order='prio')
        types = callflows.mapped('callflow_type')
        self.assertEqual(types[0], 'client')
        self.assertEqual(types[1], 'sip')
