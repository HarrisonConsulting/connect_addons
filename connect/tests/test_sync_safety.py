# -*- coding: utf-8 -*-
"""Sync safety under a partial or unexpected provider inventory.

Each case reproduces something the 2026-09-11 VoiceTel sandbox session
logged (tasks 9590, 9591, 9592, 9594, 9596).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import ConnectTestCase


def _provider_number(sid, phone_number, friendly_name=None):
    return SimpleNamespace(sid=sid, phone_number=phone_number,
                           friendly_name=friendly_name or phone_number)


@tagged('post_install', '-at_install')
class TestSyncSafety(ConnectTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Settings = cls.env['connect.settings']
        cls.CallerId = cls.env['connect.outgoing_callerid']
        cls.Number = cls.env['connect.number']
        cls.CallerId.search([]).with_context(connect_sync_removal=True).unlink()
        cls.Settings.set_param('twilio_auto_sync', True)
        # One create per record: _check_number is not multi-record safe.
        cls.callerids = cls.CallerId
        for digit, name in (('1', 'one'), ('2', 'two'), ('3', 'three')):
            cls.callerids |= cls.CallerId.with_context(skip_validation=True).create({
                'sid': 'PN' + digit * 32, 'number': '+1201555010' + digit,
                'friendly_name': name, 'callerid_type': 'number'})

    def _client(self, incoming=(), outgoing=()):
        client = MagicMock()
        client.incoming_phone_numbers.list.return_value = list(incoming)
        client.outgoing_caller_ids.list.return_value = list(outgoing)
        return client

    def _sync_callerids(self, client):
        with patch.object(type(self.Settings), 'get_client', return_value=client), \
                patch.object(type(self.Settings), 'connect_notify') as notify:
            self.CallerId.sync_outgoing_callerid('number')
        return notify

    def _as_provider(self, provider):
        real_get_param = type(self.Settings).get_param

        def get_param(settings, param, default=False):
            if param == 'rest_provider':
                return provider
            return real_get_param(settings, param, default)
        return patch.object(type(self.Settings), 'get_param', get_param)

    # 9590: a listing of one against three held must not delete two.
    def test_partial_listing_refuses_removal(self):
        kept = self.callerids[0]
        notify = self._sync_callerids(self._client(
            incoming=[_provider_number(kept.sid, kept.number)]))
        self.assertEqual(len(self.callerids.exists()), 3)
        notify.assert_called_once()
        message = notify.call_args.kwargs['message']
        self.assertIn('returned 1', message)
        self.assertIn('holds 3', message)
        self.assertIn('refusing to remove 2', message)

    def test_empty_listing_refuses_removal_visibly(self):
        notify = self._sync_callerids(self._client())
        self.assertEqual(len(self.callerids.exists()), 3)
        self.assertIn('returned 0', notify.call_args.kwargs['message'])

    # 9590 + 9591: a plausible removal still works, past the interlock.
    def test_plausible_removal_passes_interlock(self):
        listed = self.callerids[:2]
        notify = self._sync_callerids(self._client(incoming=[
            _provider_number(rec.sid, rec.number) for rec in listed]))
        self.assertEqual(self.callerids.exists(), listed)
        notify.assert_not_called()

    def test_number_sync_refuses_partial_listing(self):
        numbers = self.Number.create([
            {'phone_number': '+12015550201', 'sid': 'PN' + 'a' * 32},
            {'phone_number': '+12015550202', 'sid': 'PN' + 'b' * 32},
            {'phone_number': '+12015550203', 'sid': 'PN' + 'c' * 32},
        ])
        held = self.Number.search_count([('sid', '!=', False)])
        client = self._client(incoming=[
            _provider_number(numbers[0].sid, numbers[0].phone_number)])
        with patch.object(type(self.Settings), 'get_client', return_value=client), \
                patch.object(type(self.Settings), 'connect_notify') as notify, \
                patch.object(type(self.Number), 'update_twilio_number'):
            self.Number.sync()
        self.assertEqual(len(numbers.exists()), 3)
        self.assertIn('holds {}'.format(held), notify.call_args.kwargs['message'])

    # 9591: a manual delete is refused, names the provider, says why.
    def test_manual_delete_refused_with_provider_name(self):
        with self.assertRaises(ValidationError) as err:
            self.callerids[0].unlink()
        self.assertIn('Twilio account', str(err.exception))
        self.assertIn('turn off Auto Sync', str(err.exception))
        with self._as_provider('voicetel'), self.assertRaises(ValidationError) as err:
            self.callerids[0].unlink()
        self.assertNotIn('Twilio', str(err.exception))

    def test_manual_delete_allowed_with_auto_sync_off(self):
        self.Settings.set_param('twilio_auto_sync', False)
        self.callerids[0].unlink()
        self.assertFalse(self.callerids[0].exists())

    # 9594: a recycled SID must not move a row onto another row's number.
    def test_recycled_sid_is_skipped_not_written(self):
        one, two, three = self.callerids
        notify = self._sync_callerids(self._client(incoming=[
            _provider_number(one.sid, two.number),
            _provider_number(two.sid, two.number),
            _provider_number(three.sid, three.number),
        ]))
        self.env.flush_all()
        self.assertEqual(one.number, '+12015550101')
        self.assertEqual(len(self.callerids.exists()), 3)
        self.assertIn('+12015550102 (Odoo: +12015550101)',
                      notify.call_args.kwargs['message'])

    # 9596: no routes.v2 call under a non-Twilio provider.
    def test_routing_region_skipped_for_non_twilio(self):
        self.Number.create({'phone_number': '+12015550301', 'sid': 'PN' + 'd' * 32})
        self.Settings.set_param('twilio_region', 'us1')
        client = self._client(incoming=[_provider_number('PN' + 'd' * 32, '+12015550301')])
        with self._as_provider('voicetel'), \
                patch.object(type(self.Settings), 'get_client', return_value=client), \
                patch.object(type(self.Settings), 'connect_notify'), \
                patch.object(type(self.Number), 'update_twilio_number'):
            self.Number.sync()
        client.routes.v2.phone_numbers.assert_not_called()

    def test_routing_region_still_set_for_twilio(self):
        self.Number.create({'phone_number': '+12015550302', 'sid': 'PN' + 'e' * 32})
        self.Settings.set_param('twilio_region', 'us1')
        client = self._client(incoming=[_provider_number('PN' + 'e' * 32, '+12015550302')])
        with self._as_provider('twilio'), \
                patch.object(type(self.Settings), 'get_client', return_value=client), \
                patch.object(type(self.Settings), 'connect_notify'), \
                patch.object(type(self.Number), 'update_twilio_number'):
            self.Number.sync()
        client.routes.v2.phone_numbers.assert_called_with('+12015550302')

    # 9592: the log context names the account, never the token.
    def test_provider_log_context_names_account_not_token(self):
        with patch.object(type(self.Settings), '_get_client_credentials',
                          return_value=('AC4df298' + '0' * 26, 'SECRET-TOKEN')):
            context = self.Settings._provider_log_context()
        self.assertIn('account=AC4df298…', context)
        self.assertIn('provider=twilio', context)
        self.assertNotIn('SECRET-TOKEN', context)
