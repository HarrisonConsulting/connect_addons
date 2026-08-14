# -*- coding: utf-8 -*-
"""Common test utilities for Connect modules.

This module provides base test classes and mock infrastructure for testing
Twilio/telephony integrations without making real API calls.
"""

from contextlib import contextmanager
from unittest.mock import patch, MagicMock

from odoo.tests import TransactionCase, tagged


class MockTwilioResponse:
    """Mock Twilio API response object."""

    def __init__(self, sid=None, status='queued', **kwargs):
        self.sid = sid or 'CA' + 'x' * 32
        self.status = status
        for key, value in kwargs.items():
            setattr(self, key, value)


class MockTwilioCallInstance:
    """Mock for individual call operations."""

    def __init__(self, sid, call_data=None):
        self.sid = sid
        self._data = call_data or {}

    def fetch(self):
        return MockTwilioResponse(
            sid=self.sid,
            price='-0.015',
            price_unit='USD',
            **self._data
        )

    def update(self, **kwargs):
        self._data.update(kwargs)
        return MockTwilioResponse(sid=self.sid, **self._data)


class MockTwilioCalls:
    """Mock for Twilio calls resource."""

    def __init__(self):
        self._calls = {}
        self._call_count = 0

    def create(self, **kwargs):
        self._call_count += 1
        sid = f'CA{"x" * 30}{self._call_count:02d}'
        call = MockTwilioResponse(sid=sid, **kwargs)
        self._calls[sid] = call
        return call

    def __call__(self, sid):
        return MockTwilioCallInstance(sid, self._calls.get(sid))


class MockTwilioClient:
    """Mock Twilio Client for testing without real API calls."""

    def __init__(self):
        self.calls = MockTwilioCalls()
        self.region = 'us1'
        self.edge = 'ashburn'
        self.http_client = MagicMock()
        self.api = MagicMock()

    def __call__(self, *args, **kwargs):
        return self


@tagged('post_install', '-at_install')
class ConnectTestCase(TransactionCase):
    """Base test class for Connect module tests.

    Provides:
    - Twilio client mocking
    - Common test data fixtures
    - Helper methods for call/channel creation
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # Create test partner
        cls.partner_1 = cls.env['res.partner'].create({
            'name': 'Test Partner',
            'phone': '+15551234567',
            'mobile': '+15559876543',
            'email': 'test@example.com',
        })

        # Ensure connect.settings record exists (get_param auto-creates if missing)
        if 'connect.settings' in cls.env:
            cls.env['connect.settings'].get_param('account_sid')

        # connect.api_url backs every callback URL rendered into TwiML. A
        # freshly built database leaves it unset or pointing at localhost,
        # which connect.settings.check_api_url() rejects — so anything that
        # renders TwiML comes back as "Invalid api url!" instead of the audio
        # or dialplan under test. Pin a valid public URL so tests exercise
        # the render path rather than the config validator.
        cls.env['ir.config_parameter'].sudo().set_param(
            'connect.api_url', 'https://test.example.com')

    @contextmanager
    def mockTwilioClient(self):
        """Context manager for mocking Twilio API calls."""
        mock_client = MockTwilioClient()

        with patch.object(
            self.env['connect.settings'].__class__,
            'get_client',
            return_value=mock_client
        ):
            self._mock_twilio_client = mock_client
            yield mock_client

    @classmethod
    def _connect_domain(cls):
        """Get or create a connect.domain for tests that build a connect.user.

        connect.user.domain is required and its default resolves whichever
        connect.domain happens to exist, so on a freshly built database the
        default returns empty and the create dies on the NOT NULL constraint.
        Reuse an existing domain when the database already has one, so this
        never perturbs tests that count or sync domains.
        """
        Domain = cls.env['connect.domain']
        existing = Domain.search([('subdomain', 'not like', 'byoc')], limit=1)
        if existing:
            return existing
        app = cls.env['connect.twiml'].search([
            ('code_type', '=', 'model_method'),
            ('model', '=', 'connect.domain'),
            ('method', '=', 'route_call'),
        ], limit=1)
        if not app:
            app = cls.env['connect.twiml'].create({
                'name': 'Test Domain App',
                'code_type': 'model_method',
                'model': 'connect.domain',
                'method': 'route_call',
            })
        return Domain.with_context(no_twilio_create=True).create({
            'friendly_name': 'Test Base Domain',
            'subdomain': 'testbase',
            'application': app.id,
        })

    def _ensure_park_slot(self, number, **vals):
        """Get or create the park slot with this number.

        connect.park_slot.sync_slots() provisions slots 1..park_slot_count
        (default 9) and connect.park_slot._name_unique makes `name` UNIQUE,
        so a test that blindly creates slot 1 collides with the shipped one
        and dies on a UniqueViolation. Reuse the existing slot when it is
        already there, which keeps these tests independent of whether the
        database was provisioned.
        """
        Slot = self.env['connect.park_slot']
        slot = Slot.search([('name', '=', number)], limit=1)
        if slot:
            if vals:
                slot.write(vals)
            return slot
        return Slot.create(dict(vals, name=number))

    def _create_test_call(self, direction='incoming', status='completed', **kwargs):
        """Helper to create a test call with channels."""
        # Keys that are handled separately and must not be passed to create()
        _internal_keys = {'caller', 'called', 'create_channel'}
        call_vals = {
            'direction': direction,
            'status': status,
            'caller': kwargs.get('caller', '+15551234567'),
            'called': kwargs.get('called', '+15559876543'),
            **{k: v for k, v in kwargs.items() if k not in _internal_keys}
        }

        # Set partner if provided or auto-match
        if 'partner' in kwargs:
            call_vals['partner'] = kwargs['partner']

        call = self.env['connect.call'].create(call_vals)

        # Create associated channel if requested
        if kwargs.get('create_channel', False):
            self.env['connect.channel'].create({
                'call': call.id,
                'sid': f'CA{"x" * 30}{call.id:02d}',
                'caller': call_vals['caller'],
                'called': call_vals['called'],
                'status': status,
                'technical_direction': 'inbound' if direction == 'incoming' else 'outbound-api',
            })

        return call


class AudioTestMixin:
    """Mixin: shared fixture helpers for connect.audio tests.

    Use alongside ConnectTestCase (or any TransactionCase subclass). Keeps
    the per-test create() boilerplate out of the test body so assertions
    stay the focus.
    """

    def _make_audio(self, **kw):
        vals = {
            'name': 'Test Audio',
            'source': 'twilio_tts',
            'static_text': 'hello',
        }
        vals.update(kw)
        return self.env['connect.audio'].create(vals)

    def _make_callflow_with_prompt(self, audio):
        return self.env['connect.callflow'].create({
            'name': 'Test CF',
            'prompt_audio_id': audio.id,
        })
