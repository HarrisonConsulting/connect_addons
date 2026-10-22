# -*- coding: utf-8 -*-
"""Sync safety: generators, removal guards, savepoints, remote deletes."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, new_test_user, tagged


def _num(sid):
    return SimpleNamespace(
        sid=sid, phone_number='+1800555%04d' % int(sid[2:]), friendly_name=sid)


def _gen(sids):
    """What the SDK's iter() returns: a one-shot generator."""
    return lambda *a, **kw: (n for n in [_num(s) for s in sids])


@tagged('post_install', '-at_install')
class TestNumberSyncGuard(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Number = self.env['connect.voicetel.number']
        self.env['connect.settings'].set_param('voicetel_auto_sync', False)
        self.Number.search([]).unlink()
        for sid in ('PN1', 'PN2', 'PN3', 'PN4'):
            self.Number.create({
                'phone_number': '+1800555%04d' % int(sid[2:]), 'sid': sid})
        self.client = MagicMock()
        settings_cls = type(self.env['connect.settings'])
        self.notify = patch.object(settings_cls, 'connect_notify').start()
        patch.object(
            settings_cls, 'get_voicetel_client', return_value=self.client).start()
        self.update = patch.object(
            type(self.Number), 'update_voicetel_number', autospec=True).start()
        self.addCleanup(patch.stopall)

    def _sids(self):
        return set(self.Number.search([]).mapped('sid'))

    def test_generator_keeps_every_number_it_returned(self):
        # #230 built its keep-list from the exhausted generator and
        # removed every number.
        self.client.incoming_phone_numbers.iter.side_effect = _gen(
            ['PN1', 'PN2', 'PN3', 'PN4'])
        self.Number.sync()
        self.assertEqual(self._sids(), {'PN1', 'PN2', 'PN3', 'PN4'})

    def test_empty_list_removes_nothing(self):
        self.client.incoming_phone_numbers.iter.side_effect = _gen([])
        self.Number.sync()
        self.assertEqual(self._sids(), {'PN1', 'PN2', 'PN3', 'PN4'})
        self.assertIn('removal skipped', self.notify.call_args.kwargs['message'])

    def test_half_or_more_removes_nothing(self):
        self.client.incoming_phone_numbers.iter.side_effect = _gen(['PN1', 'PN2'])
        self.Number.sync()
        self.assertEqual(self._sids(), {'PN1', 'PN2', 'PN3', 'PN4'})
        self.assertIn('2 of the 4', self.notify.call_args.kwargs['message'])

    def test_one_gone_is_removed(self):
        self.client.incoming_phone_numbers.iter.side_effect = _gen(['PN1', 'PN2', 'PN3'])
        self.Number.sync()
        self.assertEqual(self._sids(), {'PN1', 'PN2', 'PN3'})

    def test_one_failing_update_does_not_stop_the_rest(self):
        def update(rec, client):
            if rec.sid == 'PN2':
                raise RuntimeError('boom')
        self.update.side_effect = update
        self.client.incoming_phone_numbers.iter.side_effect = _gen(
            ['PN1', 'PN2', 'PN3', 'PN4', 'PN5'])
        self.Number.sync()
        self.assertIn('PN5', self._sids())
        self.assertEqual(self.update.call_count, 5)
        self.assertIn('1 number update(s) failed', self.notify.call_args.kwargs['message'])

    def test_update_sends_post_methods(self):
        patch.stopall()
        client = MagicMock()
        number = self.Number.search([('sid', '=', 'PN1')])
        number.update_voicetel_number(client)
        kwargs = client.incoming_phone_numbers.update.call_args.kwargs
        self.assertEqual(kwargs['voice_method'], 'POST')
        self.assertEqual(kwargs['sms_method'], 'POST')
        self.assertEqual(kwargs['status_callback_method'], 'POST')

    def test_non_admin_cannot_sync(self):
        user = new_test_user(self.env, login='vt_plain_sync')
        with self.assertRaises(AccessError):
            self.Number.with_user(user).sync()
        self.client.incoming_phone_numbers.iter.assert_not_called()


@tagged('post_install', '-at_install')
class TestCallerIdSyncGuard(TransactionCase):

    def setUp(self):
        super().setUp()
        self.CallerId = self.env['connect.voicetel.outgoing_callerid']
        self.env['connect.settings'].set_param('voicetel_auto_sync', True)
        self.CallerId.search([]).with_context(voicetel_sync_unlink=True).unlink()
        for sid in ('PN1', 'PN2', 'PN3'):
            self.CallerId.with_context(skip_validation=True, voicetel_sync=True).create({
                'number': '+1800555%04d' % int(sid[2:]), 'sid': sid,
                'friendly_name': sid, 'callerid_type': 'outgoing_callerid',
                'status': 'validated'})
        self.client = MagicMock()
        settings_cls = type(self.env['connect.settings'])
        patch.object(settings_cls, 'connect_notify').start()
        patch.object(
            settings_cls, 'get_voicetel_client', return_value=self.client).start()
        self.addCleanup(patch.stopall)

    def test_generator_keeps_every_caller_id(self):
        self.client.outgoing_caller_ids.iter.side_effect = _gen(['PN1', 'PN2', 'PN3'])
        self.CallerId.sync_outgoing_callerid('outgoing_callerid', self.client)
        self.assertEqual(len(self.CallerId.search([])), 3)

    def test_sync_removal_never_deletes_remotely(self):
        self.CallerId.with_context(skip_validation=True, voicetel_sync=True).create({
            'number': '+18005550004', 'sid': 'PN4', 'friendly_name': 'PN4',
            'callerid_type': 'outgoing_callerid', 'status': 'validated'})
        self.client.outgoing_caller_ids.iter.side_effect = _gen(['PN1', 'PN2', 'PN3'])
        self.CallerId.sync_outgoing_callerid('outgoing_callerid', self.client)
        self.assertEqual(len(self.CallerId.search([])), 3)
        self.client.outgoing_caller_ids.delete.assert_not_called()
        self.client.outgoing_caller_ids.update.assert_not_called()

    def test_empty_list_removes_nothing(self):
        self.client.outgoing_caller_ids.iter.side_effect = _gen([])
        self.CallerId.sync_outgoing_callerid('outgoing_callerid', self.client)
        self.assertEqual(len(self.CallerId.search([])), 3)

    def test_non_admin_cannot_validate(self):
        user = new_test_user(self.env, login='vt_plain_validate', groups='base.group_user')
        rec = self.CallerId.search([], limit=1)
        with self.assertRaises(AccessError):
            rec.with_user(user).sudo(False).validate()
        self.client.outgoing_caller_ids.create.assert_not_called()


@tagged('post_install', '-at_install')
class TestSyncIsolation(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Settings = self.env['connect.settings']
        self.Settings.set_param('voicetel_account_sid', 'AC_TEST')
        self.Settings.sudo().set_param('voicetel_api_key', 'a' * 32)
        cls = type(self.Settings)
        self.notify = patch.object(cls, 'connect_notify').start()
        patch.object(cls, 'check_api_url', return_value=False).start()
        self.client_getter = patch.object(cls, 'get_voicetel_client').start()
        self.addCleanup(patch.stopall)
        self.syncs = {}
        for model in ('voiceml', 'domain', 'number', 'outgoing_callerid'):
            mock = MagicMock()
            self.syncs[model] = mock
            patch.object(
                type(self.env['connect.voicetel.' + model]), 'sync', mock).start()

    def test_raising_domain_sync_still_lets_numbers_sync(self):
        self.syncs['domain'].side_effect = RuntimeError('domain boom')
        self.Settings.voicetel_sync()
        self.syncs['number'].assert_called_once()
        self.syncs['outgoing_callerid'].assert_called_once()
        self.assertIn('domain boom', self.notify.call_args[0][0])

    def test_clean_run_reports_success(self):
        self.Settings.voicetel_sync()
        self.assertIn('successfully', self.notify.call_args[0][0])

    def test_non_admin_gets_access_error_before_any_client_call(self):
        user = new_test_user(self.env, login='vt_plain_isolation')
        with self.assertRaises(AccessError):
            self.Settings.with_user(user).voicetel_sync()
        self.client_getter.assert_not_called()
        for mock in self.syncs.values():
            mock.assert_not_called()
