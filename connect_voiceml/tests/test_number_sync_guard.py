# -*- coding: utf-8 -*-
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from odoo.tests.common import TransactionCase, tagged


def _num(sid):
    return SimpleNamespace(
        sid=sid, phone_number='+1800555%04d' % int(sid[2:]), friendly_name=sid)


def _page(sids, next_uri=None):
    return SimpleNamespace(
        incoming_phone_numbers=[_num(s) for s in sids], next_page_uri=next_uri)


@tagged('post_install', '-at_install')
class TestNumberSyncGuard(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Number = self.env['connect.voiceml.number']
        self.Number.search([]).unlink()
        for sid in ('PN1', 'PN2', 'PN3'):
            self.Number.create({
                'phone_number': '+1800555%04d' % int(sid[2:]), 'sid': sid})
        self.client = MagicMock()
        settings_cls = type(self.env['connect.settings'])
        self.notify = patch.object(settings_cls, 'connect_notify').start()
        patch.object(
            settings_cls, '_voiceml_client', return_value=self.client).start()
        self.update_number = patch.object(
            self.Number.__class__, 'update_number', autospec=True).start()
        self.addCleanup(patch.stopall)

    def _sids(self):
        return set(self.Number.search([]).mapped('sid'))

    def test_empty_list_removes_nothing(self):
        self.client.incoming_phone_numbers.list.return_value = _page([])
        self.Number.sync()
        self.assertEqual(self._sids(), {'PN1', 'PN2', 'PN3'})
        self.assertTrue(self.notify.called)

    def test_one_of_three_removes_nothing_and_warns(self):
        self.client.incoming_phone_numbers.list.return_value = _page(['PN1'])
        self.Number.sync()
        self.assertEqual(self._sids(), {'PN1', 'PN2', 'PN3'})
        self.assertIn('2 of 3', self.notify.call_args[0][0])
        self.assertTrue(self.notify.call_args[1].get('warning'))

    def test_two_pages_both_kept(self):
        self.client.incoming_phone_numbers.list.side_effect = [
            _page(['PN1', 'PN2'], '/x?PageToken=abc'),
            _page(['PN3']),
        ]
        self.Number.sync()
        self.assertEqual(self._sids(), {'PN1', 'PN2', 'PN3'})
        second = self.client.incoming_phone_numbers.list.call_args_list[1]
        self.assertEqual(second[1], {'page_token': 'abc'})

    def test_small_removal_is_allowed(self):
        for sid in ('PN4', 'PN5'):
            self.Number.create({
                'phone_number': '+1800555%04d' % int(sid[2:]), 'sid': sid})
        self.client.incoming_phone_numbers.list.return_value = _page(
            ['PN1', 'PN2', 'PN3', 'PN4'])
        self.Number.sync()
        self.assertEqual(self._sids(), {'PN1', 'PN2', 'PN3', 'PN4'})

    def test_page_two_failure_unlinks_nothing(self):
        self.client.incoming_phone_numbers.list.side_effect = [
            _page(['PN1'], '/x?PageToken=abc'),
            RuntimeError('boom'),
        ]
        with self.assertRaises(RuntimeError):
            self.Number.sync()
        self.assertEqual(self._sids(), {'PN1', 'PN2', 'PN3'})

    def test_failing_update_does_not_stop_the_rest(self):
        self.client.incoming_phone_numbers.list.return_value = _page(
            ['PN1', 'PN2', 'PN3'])
        calls = []

        def fake_update(rec, client):
            calls.append(rec.sid)
            if rec.sid == 'PN1':
                raise ValueError('307')

        self.update_number.side_effect = fake_update
        self.Number.sync()
        self.assertEqual(sorted(calls), ['PN1', 'PN2', 'PN3'])
        self.assertIn('PN1', self.notify.call_args[0][0])

    def test_update_sends_webhook_config(self):
        patch.stopall()
        settings_cls = type(self.env['connect.settings'])
        orig = settings_cls.get_param

        def get_param(rec, key, *a, **k):
            if key == 'api_url':
                return 'https://odoo.example.com/'
            return orig(rec, key, *a, **k)

        rec = self.Number.search([('sid', '=', 'PN1')])
        client = MagicMock()
        with patch.object(settings_cls, 'get_param', get_param):
            rec.update_number(client)
        kwargs = client.incoming_phone_numbers.update.call_args[1]
        self.assertEqual(kwargs['voice_method'], 'POST')
        self.assertEqual(kwargs['sms_method'], 'POST')
        # voiceml 0.9.3 update() accepts neither friendly_name nor status_callback.
        for dropped in ('friendly_name', 'status_callback', 'status_callback_method'):
            self.assertNotIn(dropped, kwargs)
        self.assertEqual(
            kwargs['sms_url'], 'https://odoo.example.com/voiceml/webhook/message')
