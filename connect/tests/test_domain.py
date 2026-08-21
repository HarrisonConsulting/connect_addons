# -*- coding: utf-8 -*-
"""Tests for connect.domain model: routing, SIP domain creation, and call origination."""

from unittest.mock import patch, MagicMock

from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger
from .common import ConnectTestCase


@tagged('post_install', '-at_install')
class TestDomainNameComputation(ConnectTestCase):
    """Test domain_name and edge_domains computed fields."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twiml_app = cls.env['connect.twiml'].create({
            'name': 'Test Domain App',
            'code_type': 'model_method',
            'model': 'connect.domain',
            'method': 'route_call',
        })
        cls.domain = cls.env['connect.domain'].with_context(
            no_twilio_create=True,
        ).create({
            'friendly_name': 'Test Domain',
            'subdomain': 'testco',
            'application': cls.twiml_app.id,
        })

    def test_domain_name_computation(self):
        """domain_name computed as subdomain.sip.twilio.com."""
        self.assertEqual(self.domain.domain_name, 'testco.sip.twilio.com')

    def test_domain_name_empty_subdomain(self):
        """Empty subdomain yields empty domain_name."""
        domain = self.env['connect.domain'].with_context(
            no_twilio_create=True,
        ).create({
            'friendly_name': 'Empty Sub',
            'subdomain': '',
            'application': self.twiml_app.id,
        })
        # Force recompute
        domain.invalidate_recordset(['domain_name', 'edge_domains'])
        self.assertEqual(domain.domain_name, '')
        self.assertEqual(domain.edge_domains, '')

    def test_edge_domains_contain_all_edges(self):
        """edge_domains includes all non-roaming Twilio edges."""
        self.assertIn('testco.sip.ashburn.twilio.com', self.domain.edge_domains)
        self.assertIn('testco.sip.dublin.twilio.com', self.domain.edge_domains)
        self.assertIn('testco.sip.sydney.twilio.com', self.domain.edge_domains)
        # roaming should never appear
        self.assertNotIn('roaming', self.domain.edge_domains)

    def test_domain_name_custom_sip_suffix(self):
        self.env['connect.settings'].set_param(
            'sip_domain_suffix', 'sip.voiceml.example.com')
        domain = self.env['connect.domain'].with_context(
            no_twilio_create=True,
        ).create({
            'friendly_name': 'VoiceML Domain',
            'subdomain': 'pbx',
            'application': self.twiml_app.id,
        })
        self.assertEqual(domain.domain_name, 'pbx.sip.voiceml.example.com')
        self.assertEqual(domain.edge_domains, 'pbx.sip.voiceml.example.com')


@tagged('post_install', '-at_install')
class TestCreateTwilioSipDomain(ConnectTestCase):
    """Test create_twilio_sip_domain Twilio API interactions."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twiml_app = cls.env['connect.twiml'].create({
            'name': 'Domain App',
            'code_type': 'model_method',
            'model': 'connect.domain',
            'method': 'route_call',
        })

    def _create_domain_no_twilio(self, **kwargs):
        defaults = {
            'friendly_name': 'Test Create Domain',
            'subdomain': 'testcreate',
            'application': self.twiml_app.id,
        }
        defaults.update(kwargs)
        return self.env['connect.domain'].with_context(
            no_twilio_create=True,
        ).create(defaults)

    def test_create_twilio_sip_domain(self):
        """Creating domain in Twilio sets sid and cred_list_sid correctly."""
        domain = self._create_domain_no_twilio()

        mock_client = MagicMock()
        mock_domain_response = MagicMock(sid='SD_test_domain_sid')
        mock_client.sip.domains.create.return_value = mock_domain_response
        mock_client.routes.v2.sip_domains.return_value.update.return_value = MagicMock()

        mock_cred_list = MagicMock(sid='CL_test_cred_list_sid')
        mock_client.sip.credential_lists.create.return_value = mock_cred_list

        mock_mapping = MagicMock(sid='MP_mapping_sid')
        mock_client.sip.domains.return_value.auth.registrations.credential_list_mappings.create.return_value = mock_mapping
        mock_client.sip.domains.return_value.auth.calls.credential_list_mappings.create.return_value = mock_mapping

        with patch.object(
            self.env['connect.settings'].__class__, 'get_param',
            return_value='us1',
        ), patch.object(
            self.env['connect.settings'].__class__, 'get_client',
            return_value=mock_client,
        ):
            domain.create_twilio_sip_domain(mock_client)

        self.assertEqual(domain.sid, 'SD_test_domain_sid')
        self.assertEqual(domain.cred_list_sid, 'CL_test_cred_list_sid')
        mock_client.sip.domains.create.assert_called_once()
        mock_client.sip.credential_lists.create.assert_called_once_with(
            friendly_name='SD_test_domain_sid')

    def test_create_twilio_sip_domain_already_exists(self):
        """Handles case where domain already exists in Twilio via create_domain."""
        domain = self._create_domain_no_twilio(subdomain='existing')

        mock_client = MagicMock()
        mock_client.sip.domains.create.side_effect = Exception(
            'Domain already exists in Twilio')

        with patch.object(
            self.env['connect.settings'].__class__, 'get_param',
            return_value='us1',
        ):
            with self.assertRaises(ValidationError) as ctx:
                domain.create_domain(mock_client)
            # Wording changed in 4ec7648 when domain-conflict handling was
            # reworked; the test was not updated. Assert the current contract.
            self.assertIn('already registered in Twilio', str(ctx.exception))

    def test_create_domain_generic_error(self):
        """Generic Twilio error during domain creation raises ValidationError."""
        domain = self._create_domain_no_twilio(subdomain='errordom')

        mock_client = MagicMock()
        mock_client.sip.domains.create.side_effect = Exception('API rate limit exceeded')

        with patch.object(
            self.env['connect.settings'].__class__, 'get_param',
            return_value='us1',
        ):
            with self.assertRaises(ValidationError):
                domain.create_domain(mock_client)


@tagged('post_install', '-at_install')
class TestDomainRouteCall(ConnectTestCase):
    """Test domain route_call SIP call routing logic."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Grant webhook access so connect_website's route_call override passes
        webhook_group = cls.env.ref('connect.group_connect_webhook')
        cls.env.user.group_ids = [(4, webhook_group.id)]
        cls.twiml_app = cls.env['connect.twiml'].create({
            'name': 'Route App',
            'code_type': 'model_method',
            'model': 'connect.domain',
            'method': 'route_call',
        })
        cls.domain = cls.env['connect.domain'].with_context(
            no_twilio_create=True,
        ).create({
            'friendly_name': 'Route Test Domain',
            'subdomain': 'routetest',
            'application': cls.twiml_app.id,
        })
        # Create a connect.user for routing tests
        cls.connect_user = cls.env['connect.user'].with_context(
            no_twilio_create=True,
            no_clear_cache=True,
        ).create({
            'username': 'testroute',
            'domain': cls.domain.id,
            'client_enabled': True,
        })
        # Create extension pointing to the user
        # Extension numbers are globally unique and a production-faithful
        # database already uses the short ones.
        cls.exten = cls.env['connect.exten'].create({
            'number': '8100',
            'model': 'connect.user',
            'res_id': cls.connect_user.id,
        })

    def _make_sip_request(self, to_value, **kwargs):
        """Build a minimal SIP request dict."""
        defaults = {
            'To': to_value,
            'From': 'sip:caller@routetest.sip.twilio.com',
            'Caller': 'sip:caller@routetest.sip.twilio.com',
            'Called': to_value,
            'CallSid': 'CA_route_test_001',
            'CallStatus': 'ringing',
            'Direction': 'inbound',
        }
        defaults.update(kwargs)
        return defaults

    def test_route_call_to_user_extension(self):
        """Incoming SIP call to a valid extension routes to the user."""
        request = self._make_sip_request(
            'sip:8100@routetest.sip.twilio.com')

        with self.mockTwilioClient():
            with patch.object(
                self.env['connect.call'].__class__, 'on_call_status',
            ), patch.object(
                self.exten.__class__, 'render',
                return_value='<Response><Dial><Client>testroute</Client></Dial></Response>',
            ) as mock_render:
                result = self.env['connect.domain'].route_call(request)
                mock_render.assert_called_once()

    def test_route_call_extension_not_found(self):
        """Incoming SIP call to non-existent extension triggers external call origination."""
        request = self._make_sip_request(
            'sip:+15559990000@routetest.sip.twilio.com')

        with self.mockTwilioClient():
            with patch.object(
                self.env['connect.call'].__class__, 'on_call_status',
            ), patch.object(
                self.env['connect.domain'].__class__, 'originate_external_call',
                return_value='<Response><Dial><Number>+15559990000</Number></Dial></Response>',
            ) as mock_originate:
                result = self.env['connect.domain'].route_call(request)
                mock_originate.assert_called_once()

    def test_route_call_to_callflow_extension(self):
        """Incoming SIP call to extension mapped to callflow routes correctly."""
        callflow = self.env['connect.callflow'].create({
            'name': 'Route Callflow',
        })
        callflow_exten = self.env['connect.exten'].create({
            'number': '200',
            'model': 'connect.callflow',
            'res_id': callflow.id,
        })
        request = self._make_sip_request(
            'sip:200@routetest.sip.twilio.com')

        with self.mockTwilioClient():
            with patch.object(
                self.env['connect.call'].__class__, 'on_call_status',
            ), patch.object(
                callflow_exten.__class__, 'render',
                return_value='<Response><Say>IVR</Say></Response>',
            ) as mock_render:
                result = self.env['connect.domain'].route_call(request)
                mock_render.assert_called_once()

    def test_route_call_whatsapp_no_extension(self):
        """WhatsApp call to non-existent extension returns error message."""
        request = self._make_sip_request(
            'whatsapp:+15559990001')

        with self.mockTwilioClient():
            with patch.object(
                self.env['connect.call'].__class__, 'on_call_status',
            ):
                result = self.env['connect.domain'].route_call(request)
                self.assertIn('Extension not found', result)

    def test_route_call_multiple_extensions_error(self):
        """Multiple matching extensions produces an error response."""
        # Create two extensions with overlapping regex patterns
        exten_a = self.env['connect.exten'].create({
            'number': '30.',  # regex dot matches any char
            'model': 'connect.user',
            'res_id': self.connect_user.id,
        })
        exten_b = self.env['connect.exten'].create({
            'number': '3.0',
            'model': 'connect.user',
            'res_id': self.connect_user.id,
        })

        request = self._make_sip_request(
            'sip:300@routetest.sip.twilio.com')

        with self.mockTwilioClient():
            with patch.object(
                self.env['connect.call'].__class__, 'on_call_status',
            ):
                result = self.env['connect.domain'].route_call(request)
                self.assertIn('Multiple extensions found', result)

        # Cleanup
        exten_a.unlink()
        exten_b.unlink()


@tagged('post_install', '-at_install')
class TestOriginateExternalCall(ConnectTestCase):
    """Test domain originate_external_call TwiML generation."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twiml_app = cls.env['connect.twiml'].create({
            'name': 'Originate App',
            'code_type': 'model_method',
            'model': 'connect.domain',
            'method': 'route_call',
        })
        cls.domain = cls.env['connect.domain'].with_context(
            no_twilio_create=True,
        ).create({
            'friendly_name': 'Originate Domain',
            'subdomain': 'originate',
            'application': cls.twiml_app.id,
        })
        cls.callerid = cls.env['connect.outgoing_callerid'].with_context(
            skip_validation=True,
        ).create({
            'friendly_name': 'Default CallerID',
            'number': '+15550001111',
            'callerid_type': 'number',
            'is_default': True,
        })
        cls.connect_user = cls.env['connect.user'].with_context(
            no_twilio_create=True,
            no_clear_cache=True,
        ).create({
            'username': 'originator',
            'domain': cls.domain.id,
            'client_enabled': True,
            'record_calls': False,
            'outgoing_callerid': cls.callerid.id,
        })

    def test_originate_external_call(self):
        """Outbound call origination creates TwiML with correct params."""
        request = {
            'Caller': 'sip:originator@originate.sip.twilio.com',
            'CallSid': 'CA_orig_001',
        }
        with patch.object(
            self.env['connect.settings'].__class__, 'get_param',
            side_effect=lambda param, *a, **kw: {
                'api_url': 'https://test.example.com/',
                'twilio_edge': 'ashburn',
                'call_duration_limit': '3600',
            }.get(param, ''),
        ):
            response = self.domain.originate_external_call(
                '+15559998888', request)
            twiml = str(response)
            self.assertIn('+15559998888', twiml)
            self.assertIn('+15550001111', twiml)  # Caller ID
            self.assertIn('<Dial', twiml)
            self.assertIn('<Number', twiml)

    def test_originate_external_call_with_recording(self):
        """Outbound call with recording enabled includes record attributes."""
        self.connect_user.with_context(skip_sync=True).write({
            'record_calls': True,
        })
        request = {
            'Caller': 'sip:originator@originate.sip.twilio.com',
            'CallSid': 'CA_orig_002',
        }
        with patch.object(
            self.env['connect.settings'].__class__, 'get_param',
            side_effect=lambda param, *a, **kw: {
                'api_url': 'https://test.example.com/',
                'twilio_edge': 'ashburn',
                'call_duration_limit': '3600',
            }.get(param, ''),
        ):
            response = self.domain.originate_external_call(
                '+15559998888', request)
            twiml = str(response)
            self.assertIn('record-from-answer', twiml)
            self.assertIn('recordingstatus', twiml.lower())

        # Reset
        self.connect_user.with_context(skip_sync=True).write({
            'record_calls': False,
        })

    def test_originate_call_no_callerid(self):
        """Outbound call with no caller ID returns error TwiML."""
        # Create user without callerid
        user_no_cid = self.env['connect.user'].with_context(
            no_twilio_create=True,
            no_clear_cache=True,
        ).create({
            'username': 'nocid',
            'domain': self.domain.id,
            'client_enabled': True,
        })
        # Remove default callerid temporarily
        self.callerid.is_default = False

        request = {
            'Caller': 'sip:nocid@originate.sip.twilio.com',
            'CallSid': 'CA_orig_003',
        }
        with patch.object(
            self.env['connect.settings'].__class__, 'get_param',
            side_effect=lambda param, *a, **kw: {
                'api_url': 'https://test.example.com/',
                'twilio_edge': 'ashburn',
                'call_duration_limit': '3600',
            }.get(param, ''),
        ):
            result = self.domain.originate_external_call(
                '+15559998888', request)
            self.assertIn('default number for caller ID', str(result))

        # Restore
        self.callerid.is_default = True


@tagged('post_install', '-at_install')
class TestDomainDeleteProtection(ConnectTestCase):
    """Test domain deletion protections."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twiml_app = cls.env['connect.twiml'].create({
            'name': 'Delete App',
            'code_type': 'model_method',
            'model': 'connect.domain',
            'method': 'route_call',
        })

    def test_delete_protection_enabled(self):
        """Domain with delete_protection=True raises ValidationError on unlink."""
        domain = self.env['connect.domain'].with_context(
            no_twilio_create=True,
        ).create({
            'friendly_name': 'Protected Domain',
            'subdomain': 'protected',
            'application': self.twiml_app.id,
            'delete_protection': True,
        })
        with self.assertRaises(ValidationError) as ctx:
            domain.unlink()
        self.assertIn('delete protection', str(ctx.exception))

    def test_delete_protection_disabled(self):
        """Domain with delete_protection=False can be deleted."""
        domain = self.env['connect.domain'].with_context(
            no_twilio_create=True,
        ).create({
            'friendly_name': 'Unprotected Domain',
            'subdomain': 'unprotected',
            'application': self.twiml_app.id,
            'delete_protection': False,
        })
        with patch.object(
            self.env['connect.settings'].__class__, 'get_param',
            return_value=False,  # twilio_auto_sync disabled
        ):
            domain_id = domain.id
            domain.unlink()
            self.assertFalse(
                self.env['connect.domain'].browse(domain_id).exists())

    def test_delete_protection_force_delete(self):
        """Domain with delete_protection=True can be deleted with force_delete context."""
        domain = self.env['connect.domain'].with_context(
            no_twilio_create=True,
        ).create({
            'friendly_name': 'Force Delete Domain',
            'subdomain': 'forcedel',
            'application': self.twiml_app.id,
            'delete_protection': True,
        })
        with patch.object(
            self.env['connect.settings'].__class__, 'get_param',
            return_value=False,  # twilio_auto_sync disabled
        ):
            domain_id = domain.id
            domain.with_context(force_delete=True).unlink()
            self.assertFalse(
                self.env['connect.domain'].browse(domain_id).exists())


@tagged('post_install', '-at_install')
class TestDomainGetDomainApp(ConnectTestCase):
    """Test get_domain_app auto-creation logic."""

    def test_get_domain_app_returns_existing(self):
        """get_domain_app returns existing route_call TwiML app."""
        domain = self.env['connect.domain']
        app = domain.get_domain_app()
        self.assertTrue(app)
        self.assertEqual(app.code_type, 'model_method')
        self.assertEqual(app.model, 'connect.domain')
        self.assertEqual(app.method, 'route_call')

    def test_get_domain_app_creates_if_missing(self):
        """get_domain_app creates new TwiML app if none exists."""
        # Remove all domain route_call apps. A production-faithful database
        # has SIP domains whose ``application`` column (ON DELETE RESTRICT)
        # still points at them — repoint those domains to a placeholder app
        # first so the route_call apps can actually go away.
        existing = self.env['connect.twiml'].search([
            ('code_type', '=', 'model_method'),
            ('model', '=', 'connect.domain'),
            ('method', '=', 'route_call'),
        ])
        referencing = self.env['connect.domain'].search(
            [('application', 'in', existing.ids)])
        if referencing:
            placeholder = self.env['connect.twiml'].create({
                'name': 'Placeholder while route_call is absent',
                'code_type': 'model_method',
                'model': 'connect.domain',
                'method': 'route_call_placeholder',
            })
            referencing.write({'application': placeholder.id})
        existing.unlink()

        domain = self.env['connect.domain']
        app = domain.get_domain_app()
        self.assertTrue(app)
        self.assertEqual(app.model, 'connect.domain')
        self.assertEqual(app.method, 'route_call')


@tagged('post_install', '-at_install')
class TestImportSipCredentials(ConnectTestCase):
    """Reconciling a domain's provider credential list into connect.users.

    Regression cover: connect.user.username is UNIQUE table-wide
    (connect.user._username_uniq) while a SIP username is only unique within
    its domain. Importing a second domain's "1000" credential raises
    psycopg2.UniqueViolation; that must not abort the whole transaction or
    roll back domain SID rebinds the sync already made, and cred_list_sid
    must not end up pointing at a stale provider account.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twiml_app = cls.env['connect.twiml'].create({
            'name': 'Cred Import App',
            'code_type': 'model_method',
            'model': 'connect.domain',
            'method': 'route_call',
        })
        cls.domain_a = cls._make_domain('Domain A', 'credimporta')
        cls.domain_b = cls._make_domain('Domain B', 'credimportb')

    @classmethod
    def _make_domain(cls, friendly_name, subdomain):
        return cls.env['connect.domain'].with_context(
            no_twilio_create=True,
        ).create({
            'friendly_name': friendly_name,
            'subdomain': subdomain,
            'application': cls.twiml_app.id,
        })

    def _make_user(self, domain, username, sid='CR_old'):
        return self.env['connect.user'].with_context(
            no_twilio_create=True, skip_sync=True,
        ).create({
            'username': username,
            'domain': domain.id,
            'sid': sid,
            'sip_enabled': True,
        })

    def _client_returning(self, *credentials):
        client = MagicMock()
        client.sip.credential_lists.return_value.credentials.list.return_value = list(credentials)
        return client

    @staticmethod
    def _credential(username, sid):
        return MagicMock(username=username, sid=sid)

    def _find_users(self, username):
        return self.env['connect.user'].with_context(active_test=False).search(
            [('username', '=', username)])

    def test_credential_matching_this_domain_updates_sid(self):
        """A credential whose username already exists here rebinds the SID."""
        user = self._make_user(self.domain_b, 'ext2000', sid='CR_stale')
        client = self._client_returning(self._credential('ext2000', 'CR_fresh'))

        self.domain_b._import_sip_credentials_from_twilio(client, 'CL_b')

        self.assertEqual(self._find_users('ext2000'), user)
        self.assertEqual(user.sid, 'CR_fresh')

    def test_unknown_credential_creates_user(self):
        """A credential with no counterpart in Odoo materializes a user."""
        client = self._client_returning(self._credential('ext3000', 'CR_new'))

        self.domain_b._import_sip_credentials_from_twilio(client, 'CL_b')

        created = self._find_users('ext3000')
        self.assertEqual(len(created), 1)
        self.assertEqual(created.domain, self.domain_b)
        self.assertEqual(created.sid, 'CR_new')
        self.assertTrue(created.sip_enabled)

    def test_username_owned_by_other_domain_is_skipped(self):
        """The same extension on two domains must not duplicate the user."""
        user = self._make_user(self.domain_a, 'ext1000', sid='CR_a')
        client = self._client_returning(self._credential('ext1000', 'CR_b'))

        with mute_logger('odoo.addons.connect.models.domain'):
            self.domain_b._import_sip_credentials_from_twilio(client, 'CL_b')

        found = self._find_users('ext1000')
        self.assertEqual(found, user, "must not create a second ext1000")
        self.assertEqual(found.domain, self.domain_a, "must not steal the user")
        self.assertEqual(found.sid, 'CR_a', "must not rebind another domain's SID")

    def test_collision_does_not_abort_the_transaction(self):
        """The caller's earlier writes survive a colliding credential.

        The domain rebind that sync performs immediately before the import
        must survive a colliding-credential constraint violation: it must not
        be rolled back, and cred_list_sid must not end up pointing at a stale
        account (which would 404 every later credential mint).
        """
        self._make_user(self.domain_a, 'ext1000', sid='CR_a')
        # Stand in for the SID rebind _import_existing_domain_by_name does
        # just before calling the credential import.
        self.domain_b.write({'cred_list_sid': 'CL_rebound'})
        client = self._client_returning(self._credential('ext1000', 'CR_b'))

        with mute_logger('odoo.addons.connect.models.domain'):
            self.domain_b._import_sip_credentials_from_twilio(client, 'CL_rebound')

        # Both of these raise InFailedSqlTransaction on an aborted cursor.
        self.env.flush_all()
        self.env.cr.execute('SELECT 1')

        self.domain_b.invalidate_recordset()
        self.assertEqual(self.domain_b.cred_list_sid, 'CL_rebound')

    def test_archived_user_holding_username_is_not_duplicated(self):
        """An archived user still occupies the username table-wide."""
        user = self._make_user(self.domain_b, 'ext4000', sid='CR_old')
        user.active = False
        client = self._client_returning(self._credential('ext4000', 'CR_new'))

        self.domain_b._import_sip_credentials_from_twilio(client, 'CL_b')

        found = self._find_users('ext4000')
        self.assertEqual(len(found), 1)
        self.assertEqual(found.sid, 'CR_new')
