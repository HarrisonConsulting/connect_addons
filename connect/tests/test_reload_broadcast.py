# -*- coding: utf-8 -*-
"""connect.call.on_call_status reload_view broadcast gating.

Regression cover: per-leg webhook status events must not each broadcast a
reload_view on the shared, org-wide ``connect_actions`` bus channel. An
unthrottled call can hunt a non-answering agent for hours, emitting one
status event per second — broadcasting unthrottled would refresh every
open connect.call view in the company and make the voicemail pages
unusable.

Only three transitions render differently in a call list/kanban: the call
appearing, the call settling at finalization, and the call going into error.
Everything else the per-leg webhooks touch is churn.
"""

from unittest.mock import patch

from odoo.tests import tagged
from .common import ConnectTestCase


@tagged('post_install', '-at_install')
class TestReloadBroadcast(ConnectTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Call = cls.env['connect.call']

    def _params(self, **overrides):
        params = {
            'CallSid': 'CAreload0000000000000000000000001',
            'CallStatus': 'initiated',
            'Direction': 'inbound',
            'Caller': '+15551234567',
            'Called': '+15559876543',
            'To': '+15559876543',
            'CallDuration': '0',
            'SequenceNumber': '1',
        }
        params.update(overrides)
        return params

    def _reload_calls(self, params):
        """Run on_call_status and return the models it broadcast a reload for."""
        with patch.object(
                type(self.env['connect.settings']),
                'connect_reload_view') as reload_view:
            self.Call.on_call_status(params)
        return [c.args[0] for c in reload_view.call_args_list]

    def test_first_webhook_creates_call_and_broadcasts(self):
        """A new row in the list is worth a reload."""
        models = self._reload_calls(self._params())
        self.assertIn('connect.call', models)

    def test_intermediate_child_leg_webhook_is_silent(self):
        """The agent-hunt case: child legs churning through
        initiated/no-answer must not reload every user's view."""
        self.Call.on_call_status(self._params())
        child = self._params(
            CallSid='CAreload0000000000000000000000002',
            ParentCallSid='CAreload0000000000000000000000001',
            CallStatus='initiated',
            SequenceNumber='2',
        )
        self.assertEqual(self._reload_calls(child), [])

    def test_error_webhook_broadcasts_once(self):
        """The first error stamps has_error and reloads; a repeat of the same
        error does not re-broadcast."""
        self.Call.on_call_status(self._params())
        err = self._params(
            CallSid='CAreload0000000000000000000000003',
            ParentCallSid='CAreload0000000000000000000000001',
            CallStatus='no-answer',
            SequenceNumber='2',
            ErrorCode='13224',
            ErrorMessage='Dial: Twilio does not support calling this number',
        )
        self.assertIn('connect.call', self._reload_calls(err))
        self.assertEqual(self._reload_calls(err), [])
