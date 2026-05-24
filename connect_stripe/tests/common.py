# -*- coding: utf-8 -*-
"""Shared fixtures for connect_stripe tests.

The module charges via Odoo's payment.transaction pipeline, so the test
infrastructure mocks `payment.provider._send_api_request` (and the
transaction-side equivalent) rather than the stripe SDK directly. This works
across HttpCase worker threads too — patching the class attribute is global.
"""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from odoo.addons.connect.tests.common import ConnectTestCase


# ----- Twilio: record-and-replay mock ----------------------------------- #

class _RecordingCallInstance:
    def __init__(self, sid, updates_ref):
        self.sid = sid
        self._updates = updates_ref
        self._data = updates_ref.setdefault(sid, {})

    def fetch(self):
        return SimpleNamespace(sid=self.sid, status='in-progress')

    def update(self, **kwargs):
        self._updates.setdefault(self.sid, {}).update(kwargs)
        self._data = self._updates[self.sid]
        return SimpleNamespace(sid=self.sid, **kwargs)


class _RecordingCalls:
    def __init__(self):
        self.updates = {}

    def __call__(self, sid):
        return _RecordingCallInstance(sid, self.updates)


class _RecordingTwilioClient:
    def __init__(self):
        self.calls = _RecordingCalls()
        self.region = 'us1'


# ----- Stripe HTTP mock ------------------------------------------------- #

class StripeApiRecorder:
    """Record all _send_api_request calls and return canned responses."""

    def __init__(self):
        self.calls = []
        self._next_intent_status = 'succeeded'

    def _make_customer(self, method, data, **_):
        email = (data.get('email') or 'anon').replace('@', '_').replace('.', '_')
        return {'id': f'cus_test_{email}', 'object': 'customer'}

    def _attach_payment_method(self, method, data, **_):
        return {'id': 'pm_attached', 'customer': data.get('customer')}

    def _make_payment_intent(self, method, data, **_):
        pm = data.get('payment_method', 'pm_test')
        return {
            'id': f'pi_test_{pm}',
            'object': 'payment_intent',
            'status': self._next_intent_status,
            'amount': int(data.get('amount', 0)),
            'currency': data.get('currency'),
            'customer': data.get('customer'),
            'payment_method': {'id': pm, 'type': 'card', 'card': {'brand': 'visa'}},
            'charges': {'data': [{'payment_method_details': {'type': 'card', 'card': {}}}]},
            'last_payment_error': None,
        }

    def _make_refund(self, method, data, **_):
        return {
            'id': 'rf_test_' + (data.get('payment_intent') or 'unknown'),
            'object': 'refund',
            'status': 'succeeded',
        }

    def dispatch(self, method, endpoint, data):
        # Endpoint-prefix dispatch.
        if endpoint.startswith('customers'):
            return self._make_customer(method, data)
        if endpoint.startswith('payment_methods/'):
            return self._attach_payment_method(method, data)
        if endpoint.startswith('payment_intents'):
            return self._make_payment_intent(method, data)
        if endpoint.startswith('refunds'):
            return self._make_refund(method, data)
        return {}


# ----- Base test case --------------------------------------------------- #

class StripeTestCase(ConnectTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.bank_journal = cls.env['account.journal'].search(
            [('type', '=', 'bank'), ('company_id', '=', cls.env.company.id)],
            limit=1,
        ) or cls.env['account.journal'].create({
            'name': 'Test Bank',
            'type': 'bank',
            'code': 'TBNK',
            'company_id': cls.env.company.id,
        })

        # Stripe payment.provider — required by the new architecture.
        Provider = cls.env['payment.provider'].sudo()
        provider = Provider.search([('code', '=', 'stripe')], limit=1)
        if not provider:
            provider = Provider.create({
                'name': 'Stripe (Test)',
                'code': 'stripe',
                'state': 'test',
                'company_id': cls.env.company.id,
                'stripe_secret_key': 'sk_test_dummy',
                'stripe_publishable_key': 'pk_test_dummy',
                'stripe_webhook_secret': 'whsec_test_dummy',
            })
        else:
            provider.write({
                'state': 'test',
                'stripe_secret_key': 'sk_test_dummy',
                'stripe_publishable_key': 'pk_test_dummy',
                'stripe_webhook_secret': 'whsec_test_dummy',
            })
        if not provider.journal_id:
            provider.journal_id = cls.bank_journal.id
        cls.provider = provider

        settings = cls.env['connect.settings'].sudo().search([], limit=1)
        if not settings:
            settings = cls.env['connect.settings'].sudo().create({})
        settings.write({
            'stripe_pay_connector_name': 'TestConnector',
            'stripe_default_currency_id': cls.env.company.currency_id.id,
        })
        cls.settings = settings

        cls.call = cls._make_stripe_call(cls)

    def _make_stripe_call(self):
        return self.env['connect.call'].create({
            'direction': 'incoming',
            'status': 'in-progress',
            'caller': '+15551234567',
            'called': '+15559876543',
            'call_sid': 'CA' + 'a' * 32,
            'partner': self.partner_1.id,
        })

    @contextmanager
    def mockTwilioClient(self):
        client = _RecordingTwilioClient()
        with patch.object(
            self.env['connect.settings'].__class__,
            'get_client',
            return_value=client,
        ):
            self._mock_twilio_client = client
            yield client

    @contextmanager
    def mockStripeApi(self, intent_status='succeeded'):
        recorder = StripeApiRecorder()
        recorder._next_intent_status = intent_status

        Provider = self.env['payment.provider'].__class__
        Transaction = self.env['payment.transaction'].__class__

        def _send(_self, method, endpoint, *args, **kwargs):
            data = kwargs.get('data') or (args[0] if args else {}) or {}
            recorder.calls.append({
                'method': method,
                'endpoint': endpoint,
                'data': dict(data) if hasattr(data, 'items') else data,
                'idempotency_key': kwargs.get('idempotency_key'),
            })
            return recorder.dispatch(method, endpoint, data)

        with patch.object(Provider, '_send_api_request', _send), \
             patch.object(Transaction, '_send_api_request', _send):
            yield recorder
