# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError
from odoo.service.model import get_public_method
from odoo.tests import TransactionCase, tagged

# Methods that hand out the API key, send with the account, or act on
# unsigned webhook input. Only the signature-checked controllers (or the
# server itself) may call them, so none may be reachable through
# /web/dataset/call_kw.
PRIVATE_ENTRY_POINTS = (
    ('connect.settings', '_get_media_auth'),
    ('connect.settings', 'get_voicetel_client'),
    ('connect.call', 'on_voicetel_call_status'),
    ('connect.call', 'on_voicetel_vm_recording_status'),
    ('connect.call', 'on_voicetel_call_action'),
    ('connect.channel', 'on_voicetel_call_status'),
    ('connect.recording', 'on_voicetel_recording_status'),
    ('connect.recording', 'voicetel_download'),
    ('connect.message', 'voicetel_receive'),
    ('connect.message', 'voicetel_client_send'),
    ('connect.voicetel.domain', 'route_call'),
    ('connect.voicetel.domain', 'originate_external_call'),
    ('connect.voicetel.number', 'route_call'),
    ('connect.voicetel.number', 'render'),
    ('connect.voicetel.callflow', 'gather_action'),
    ('connect.voicetel.callflow', 'voicetel_on_call_action'),
    ('connect.voicetel.callflow', 'render'),
    ('connect.voicetel.exten', 'render'),
    ('connect.voicetel.voiceml', 'render'),
    ('connect.voicetel.outgoing_callerid', 'update_status'),
    ('connect.user', 'voicetel_on_call_action'),
    ('connect.user', 'voicetel_delete_sip_account'),
    ('connect.user', 'voicetel_render'),
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
