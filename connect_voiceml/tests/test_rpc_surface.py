# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError
from odoo.service.model import get_public_method
from odoo.tests import TransactionCase, tagged

# Methods that hand out the API key or act on unsigned webhook input. Only
# the signature-checked controllers may call them, so none may be reachable
# through /web/dataset/call_kw.
PRIVATE_ENTRY_POINTS = (
    ('connect.settings', '_get_media_auth'),
    ('connect.call', '_on_call_status_voiceml'),
    ('connect.call', '_on_call_action_voiceml'),
    ('connect.call', '_on_vm_recording_status_voiceml'),
    ('connect.channel', '_on_call_status_voiceml'),
    ('connect.recording', '_on_recording_status_voiceml'),
    ('connect.message', '_receive_voiceml'),
    ('connect.voiceml.number', '_route_call'),
    ('connect.voiceml.domain', '_route_call'),
    ('connect.voiceml.outgoing_callerid', '_update_status'),
    ('connect.voiceml.callflow', '_gather_action'),
    ('connect.voiceml.callflow', '_on_call_action'),
)


@tagged('post_install', '-at_install')
class TestRpcSurface(TransactionCase):

    def test_webhook_entry_points_are_not_rpc_callable(self):
        for model_name, method in PRIVATE_ENTRY_POINTS:
            with self.subTest(model=model_name, method=method):
                model = self.env[model_name]
                self.assertTrue(callable(getattr(model, method, None)))
                with self.assertRaises(AccessError):
                    get_public_method(model, method)

    def test_foreign_voicemail_url_is_refused(self):
        result = self.env['connect.call'].sudo()._on_vm_recording_status_voiceml({
            'CallSid': 'CA-no-such-call',
            'RecordingUrl': 'http://169.254.169.254/latest/meta-data/',
            'RecordingDuration': '3',
        })
        self.assertFalse(result)
