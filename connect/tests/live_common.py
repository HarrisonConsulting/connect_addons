# -*- coding: utf-8 -*-
"""Tier 2 test infrastructure: real Twilio test credentials, zero cost.

Uses Twilio's test credentials and magic numbers to exercise the real API
without making actual calls or incurring charges.

Setup:
    1. Copy .env.test.example to .env.test
    2. Add your Twilio test credentials (Console → Test Credentials)
    3. Run: gdo test -d <db> -i connect -T live_twilio

Credentials are loaded from environment or .env.test file.
Never committed to the repository.
"""

import functools
import logging
import os
from pathlib import Path

from urllib.parse import urlsplit

from unittest.mock import patch

from twilio.rest import Client

from odoo.tests import TransactionCase, tagged

logger = logging.getLogger(__name__)

# Twilio magic test numbers (publicly documented)
# https://www.twilio.com/docs/iam/test-credentials
MAGIC_NUMBERS = {
    'valid': '+15005550006',
    'invalid': '+15005550001',
    'cant_route': '+15005550002',
    'international_blocked': '+15005550003',
    'blacklisted': '+15005550004',
    'not_owned': '+15005550009',
    'queue': '+15005550010',
    'sip_valid': '+15005550006',
}

# Call status codes returned by test credentials
CALL_STATUSES = {
    'queued', 'ringing', 'in-progress', 'completed',
    'busy', 'failed', 'no-answer', 'canceled',
}


def _load_env_file():
    """Load .env.test from the connect_addons root."""
    env_paths = [
        Path(__file__).resolve().parent.parent.parent / '.env.test',
        Path('/workspace/HarrisonConsulting/connect_addons/.env.test'),
    ]
    for env_path in env_paths:
        if env_path.exists():
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, _, value = line.partition('=')
                        os.environ.setdefault(key.strip(), value.strip())
            return True
    return False


def get_test_credentials():
    """Return (account_sid, auth_token) or (None, None) if not configured."""
    _load_env_file()
    sid = os.environ.get('TWILIO_TEST_ACCOUNT_SID')
    token = os.environ.get('TWILIO_TEST_AUTH_TOKEN')
    return sid, token


def requires_twilio_test_creds(func):
    """Decorator: skip test if Twilio test credentials aren't available."""
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        sid, token = get_test_credentials()
        if not sid or not token:
            self.skipTest('Twilio test credentials not configured (set TWILIO_TEST_ACCOUNT_SID/TOKEN or create .env.test)')
        return func(self, *args, **kwargs)
    return wrapper


class TwilioBudgetGuard:
    """Tracks estimated Twilio spend during a test session.

    Aborts test execution if the estimated cost exceeds the configured budget.
    Only relevant for Tier 3 (live calls). Tier 2 test credentials are free.
    """

    def __init__(self, max_budget_usd=5.00):
        self.max_budget = max_budget_usd
        self.spent = 0.0
        self.calls_made = 0
        self.sms_sent = 0
        self.recordings = 0

    def record_call(self, duration_seconds=30):
        cost = (duration_seconds / 60) * 0.022
        self.spent += cost
        self.calls_made += 1
        self._check()

    def record_sms(self):
        self.spent += 0.0079
        self.sms_sent += 1
        self._check()

    def record_recording(self, duration_seconds=30):
        cost = (duration_seconds / 60) * 0.0025
        self.spent += cost
        self.recordings += 1
        self._check()

    def _check(self):
        if self.spent >= self.max_budget:
            raise RuntimeError(
                f"Test budget ${self.max_budget:.2f} exceeded! "
                f"Spent: ${self.spent:.4f} "
                f"({self.calls_made} calls, {self.sms_sent} SMS, {self.recordings} recordings)"
            )

    def summary(self):
        return (
            f"Budget: ${self.spent:.4f} / ${self.max_budget:.2f} "
            f"({self.calls_made} calls, {self.sms_sent} SMS, {self.recordings} recordings)"
        )


TWILIO_API_HOSTS = frozenset({
    'api.twilio.com',
    'conversations.twilio.com',
    'messaging.twilio.com',
    'verify.twilio.com',
})


@tagged('post_install', '-at_install', 'live_twilio')
class TwilioLiveTestCase(TransactionCase):
    """Base class for Tier 2 integration tests using Twilio test credentials.

    Tests tagged 'live_twilio' are skipped by default.
    Run with: gdo test -d <db> -T live_twilio

    Allows outbound HTTP requests to Twilio API domains only.
    All other external requests remain blocked.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Allow external HTTP requests to Twilio API during these tests.
        # Odoo's test framework blocks all non-localhost requests by default.
        # We patch the request handler to let Twilio API calls through.
        # Odoo's test framework blocks all external HTTP requests by patching
        # requests.Session.send. We need to allow Twilio API calls through.
        # Odoo stores the original send as _super_send in odoo.tests.common.
        import odoo.tests.common as otc
        real_send = getattr(otc, '_super_send', None)
        if real_send is None:
            import requests.sessions
            real_send = requests.sessions.Session.send

        original_handler = cls._request_handler.__func__

        def _twilio_handler(kls, s, r, **kw):
            url = urlsplit(r.url)
            if url.hostname in TWILIO_API_HOSTS:
                return real_send(s, r, **kw)
            return original_handler(kls, s, r, **kw)

        cls._request_handler = classmethod(_twilio_handler)
        cls.addClassCleanup(setattr, cls, '_request_handler', classmethod(original_handler))
        sid, token = get_test_credentials()
        if not sid or not token:
            raise cls.failureException(
                'Twilio test credentials required. '
                'Set TWILIO_TEST_ACCOUNT_SID/TOKEN or create .env.test'
            )
        cls.twilio_test_sid = sid
        cls.twilio_test_token = token
        cls.twilio_client = Client(sid, token)
        cls.budget = TwilioBudgetGuard(max_budget_usd=5.00)

        # Magic numbers
        cls.VALID_NUMBER = MAGIC_NUMBERS['valid']
        cls.INVALID_NUMBER = MAGIC_NUMBERS['invalid']
        cls.CANT_ROUTE = MAGIC_NUMBERS['cant_route']
        cls.NOT_OWNED = MAGIC_NUMBERS['not_owned']

        # Ensure connect settings exist
        if 'connect.settings' in cls.env:
            cls.env['connect.settings'].get_param('account_sid')

        # Create test partner
        cls.partner = cls.env['res.partner'].create({
            'name': 'Twilio Test Partner',
            'phone': cls.VALID_NUMBER,
            'email': 'twiliotest@example.com',
        })

    def _configure_test_credentials(self):
        """Temporarily configure Odoo settings with test credentials."""
        settings = self.env['connect.settings']
        settings.set_param('account_sid', self.twilio_test_sid)
        settings.set_param('auth_token', self.twilio_test_token)

    def _make_test_call(self, to=None, from_=None, twiml='<Response><Say>Test</Say></Response>'):
        """Create a call via Twilio test API. Returns the call resource."""
        to = to or self.VALID_NUMBER
        from_ = from_ or self.VALID_NUMBER
        call = self.twilio_client.calls.create(
            to=to,
            from_=from_,
            twiml=twiml,
        )
        self.budget.record_call()
        return call

    def _send_test_sms(self, to=None, from_=None, body='Test message'):
        """Send an SMS via Twilio test API. Returns the message resource."""
        to = to or self.VALID_NUMBER
        from_ = from_ or self.VALID_NUMBER
        message = self.twilio_client.messages.create(
            to=to,
            from_=from_,
            body=body,
        )
        self.budget.record_sms()
        return message
