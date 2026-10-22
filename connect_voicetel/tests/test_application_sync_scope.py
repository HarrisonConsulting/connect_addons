# -*- coding: utf-8 -*-
"""VoiceML applications: dispatch allowlist, sandbox, sync scope."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from voiceml.exceptions import NotFoundError

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestApplicationSyncScope(TransactionCase):

    def setUp(self):
        super().setUp()
        self.env['ir.config_parameter'].sudo().set_param(
            'connect.api_url', 'https://odoo.example.com/')
        self.App = self.env['connect.voicetel.voiceml'].with_context(
            no_voicetel_create=True, skip_voicetel_sync=True)
        self.client = MagicMock()
        patch.object(type(self.env['connect.settings']), 'get_voicetel_client',
                     return_value=self.client).start()
        self.addCleanup(patch.stopall)

    def test_foreign_host_application_is_left_alone(self):
        app = self.App.create({'name': 'Local', 'sid': 'AP-local'})
        self.client.applications.get.return_value = SimpleNamespace(
            sid='AP-local', voice_url='https://other.example.org/hook')
        app.update_voicetel_app(self.client)
        self.client.applications.update.assert_not_called()
        self.client.applications.create.assert_not_called()

    def test_own_application_is_updated_with_post(self):
        app = self.App.create({'name': 'Local', 'sid': 'AP-local'})
        self.client.applications.get.return_value = SimpleNamespace(
            sid='AP-local', voice_url='https://odoo.example.com/old')
        app.update_voicetel_app(self.client)
        sid, body = self.client.applications.update.call_args[0]
        self.assertEqual(sid, 'AP-local')
        self.assertEqual(body.voice_method, 'POST')
        self.assertEqual(body.status_callback_method, 'POST')

    def test_missing_application_is_recreated(self):
        app = self.App.create({'name': 'Local', 'sid': 'AP-gone'})
        self.client.applications.get.side_effect = NotFoundError('gone', status_code=404)
        self.client.applications.create.return_value = SimpleNamespace(sid='AP-new')
        app.update_voicetel_app(self.client)
        self.assertEqual(app.sid, 'AP-new')
        self.assertEqual(
            self.client.applications.create.call_args[0][0].voice_method, 'POST')

    def test_dangerous_target_cannot_be_saved(self):
        for model, method in (('res.users', 'unlink'), ('res.users', 'write'),
                              ('connect.voicetel.domain', '_route_call')):
            with self.subTest(method=method), self.assertRaises(ValidationError):
                self.App.create({'name': 'Bad', 'code_type': 'model_method',
                                 'model': model, 'method': method})

    def test_dangerous_target_in_data_renders_reject(self):
        app = self.App.create({'name': 'Bad', 'voiceml': '<Response/>'})
        self.env.cr.execute(
            "UPDATE connect_voicetel_voiceml SET code_type = 'model_method',"
            " model = 'res.users', method = 'unlink' WHERE id = %s", (app.id,))
        app.invalidate_recordset()
        with patch.object(type(self.env['connect.settings']), 'check_api_url',
                          return_value=False):
            self.assertIn('<Reject', app.render({}))

    def test_seeded_route_app_still_routes(self):
        app = self.env.ref('connect_voicetel.domain_route_call')
        with patch.object(type(self.env['connect.settings']), 'check_api_url',
                          return_value=False), \
                patch.object(type(self.env['connect.voicetel.domain']), 'route_call',
                             return_value='<Response/>') as route:
            self.assertEqual(app.render({'a': 1}, {}), '<Response/>')
        route.assert_called_once()

    def test_template_is_sandboxed_and_escaped(self):
        app = self.App.create({
            'name': 'Tpl',
            'voiceml': '<Response><Say>{{ From }}</Say></Response>',
        })
        with patch.object(type(self.env['connect.settings']), 'check_api_url',
                          return_value=False):
            xml = app.render({'From': '</Say><Dial>+19005550000</Dial><Say>'})
        self.assertNotIn('<Dial>', xml)
        self.assertIn('&lt;Dial&gt;', xml)
        evil = self.App.create({
            'name': 'Evil',
            'voiceml': "{{ ''.__class__.__mro__ }}",
        })
        with patch.object(type(self.env['connect.settings']), 'check_api_url',
                          return_value=False), self.assertRaises(Exception):
            evil.render({})

    def test_python_code_type_is_gone(self):
        self.assertNotIn(
            'python',
            dict(self.env['connect.voicetel.voiceml']._fields['code_type'].selection))
