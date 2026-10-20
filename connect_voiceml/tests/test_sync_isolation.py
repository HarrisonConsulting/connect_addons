# -*- coding: utf-8 -*-
from unittest.mock import MagicMock, patch

from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSyncIsolation(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Settings = self.env['connect.settings']
        self.settings = self.Settings.search([], limit=1)
        if not self.settings:
            self.settings = self.Settings.create({})
        self.settings.with_context(skip_protected_fields=True).sudo().write({
            'voiceml_account_sid': 'AC_TEST_ACCOUNT_SID',
            'voiceml_api_key': 'a' * 64,
        })
        cls = type(self.Settings)
        self.notify = patch.object(cls, 'connect_notify').start()
        patch.object(cls, 'check_api_url', return_value=False).start()
        self.client_getter = patch.object(cls, '_voiceml_client').start()
        self.addCleanup(patch.stopall)
        self.syncs = {}
        for model in ('application', 'domain', 'number', 'outgoing_callerid'):
            mock = MagicMock()
            self.syncs[model] = mock
            patch.object(
                type(self.env['connect.voiceml.' + model]), 'sync', mock).start()

    def test_raising_domain_sync_still_lets_numbers_sync(self):
        self.syncs['domain'].side_effect = RuntimeError('domain boom')
        self.Settings.sync_voiceml()
        self.syncs['number'].assert_called_once()
        self.syncs['outgoing_callerid'].assert_called_once()
        self.assertIn('domain boom', self.notify.call_args[0][0])
        self.assertIn('domain', self.notify.call_args[0][0])

    def test_clean_run_reports_success(self):
        self.Settings.sync_voiceml()
        self.assertIn('successfully', self.notify.call_args[0][0])

    def test_non_admin_gets_access_error_before_any_client_call(self):
        user = self.env['res.users'].create({
            'name': 'Plain', 'login': 'plain_voiceml_user',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        with self.assertRaises(AccessError):
            self.Settings.with_user(user).sync_voiceml()
        self.client_getter.assert_not_called()
        for mock in self.syncs.values():
            mock.assert_not_called()
