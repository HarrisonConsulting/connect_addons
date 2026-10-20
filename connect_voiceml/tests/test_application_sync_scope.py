# -*- coding: utf-8 -*-
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestApplicationSyncScope(TransactionCase):

    def setUp(self):
        super().setUp()
        self.env['connect.settings'].set_param('api_url', 'https://odoo.example.com/')
        self.App = self.env['connect.voiceml.application'].with_context(install_mode=True)

    def _remote(self, sid, voice_url, friendly_name='x'):
        return SimpleNamespace(sid=sid, voice_url=voice_url, friendly_name=friendly_name)

    def _sync(self, remotes):
        client = MagicMock()
        client.applications.list.return_value = SimpleNamespace(applications=remotes)
        with patch.object(type(self.env['connect.settings']), '_voiceml_client', return_value=client):
            self.env['connect.voiceml.application'].sync()
        return client

    def test_foreign_host_remote_app_untouched(self):
        client = self._sync([
            self._remote('AP-foreign', 'https://other.example.org/hook'),
            self._remote('AP-null', 'https://other.example.org/hook', friendly_name=None),
        ])
        client.applications.update.assert_not_called()
        client.applications.create.assert_not_called()
        self.assertFalse(self.env['connect.voiceml.application'].search([('sid', 'in', ['AP-foreign', 'AP-null'])]))

    def test_unknown_remote_app_not_imported_even_same_host(self):
        self._sync([self._remote('AP-new', 'https://odoo.example.com/hook', friendly_name=None)])
        self.assertFalse(self.env['connect.voiceml.application'].search([('sid', '=', 'AP-new')]))

    def test_local_app_updated_with_post(self):
        rec = self.App.create({'name': 'Local', 'sid': 'AP-local'})
        client = self._sync([self._remote('AP-local', 'https://odoo.example.com/old')])
        client.applications.update.assert_called_once()
        request = client.applications.update.call_args[0][1]
        self.assertEqual(client.applications.update.call_args[0][0], rec.sid)
        self.assertEqual(request.voice_method, 'POST')

    def test_dangerous_target_refused(self):
        for model, method in (('res.users', 'unlink'), ('res.users', 'write'), ('res.users', '_create')):
            app = self.App.create({'name': 'Bad', 'code_type': 'model_method', 'model': model, 'method': method})
            self.assertIn('<Reject', app.render({}))

    def test_seeded_route_app_still_routes(self):
        app = self.env.ref('connect_voiceml.domain_route_call')
        with patch.object(type(self.env['connect.voiceml.domain']), '_route_call',
                          return_value='<Response/>') as route:
            self.assertEqual(app.render({'a': 1}, {}), '<Response/>')
        route.assert_called_once()
