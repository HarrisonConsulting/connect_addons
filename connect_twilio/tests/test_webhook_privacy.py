# -*- coding: utf-8 -*-
"""Webhook handlers are not RPC endpoints.

The handlers trust their params because the controller checked the Twilio
signature first. Over /web/dataset/call_kw nothing checks it, so any
logged-in user (portal included) could forge a call event.
"""
from odoo.exceptions import AccessError
from odoo.service.model import get_public_method
from odoo.tests import tagged

from .common import TwilioTestCommon

WEBHOOK_HANDLERS = [
    ('connect.call', 'on_call_action'),
    ('connect.call', 'on_call_status'),
    ('connect.call', 'on_vm_recording_status'),
    ('connect.channel', 'on_call_status'),
    ('connect.message', 'receive'),
    ('connect.recording', 'on_recording_status'),
    ('connect.recording', 'update_transcript'),
    ('connect.twilio.domain', 'route_call'),
    ('connect.twilio.number', 'route_call'),
    ('connect.twilio.number', 'render'),
    ('connect.twilio.exten', 'render'),
    ('connect.twilio.callflow', 'gather_action'),
    ('connect.twilio.callflow', 'on_call_action'),
    ('connect.twilio.callflow', 'render'),
    ('connect.twilio.twiml', 'render'),
    ('connect.twilio.outgoing_callerid', 'update_status'),
    ('connect.whatsapp_sender', 'update_message_status'),
    ('connect.user', 'on_call_action'),
    ('connect.user', 'render'),
]


@tagged('at_install', '-post_install')
class TestWebhookHandlersArePrivate(TwilioTestCommon):

    def test_webhook_handlers_cannot_be_called_remotely(self):
        for model_name, method in WEBHOOK_HANDLERS:
            with self.subTest(model=model_name, method=method):
                with self.assertRaises(AccessError):
                    get_public_method(self.env[model_name], method)

