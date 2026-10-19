# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase


class TestNumberRouting(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Settings = self.env['connect.settings']
        self.settings = self.Settings.search([], limit=1)
        if not self.settings:
            self.settings = self.Settings.create({})
        self.Application = self.env['connect.voiceml.application']

    def _make_application(self, twiml):
        return self.Application.with_context(install_mode=True).create({
            'name': 'Test App',
            'code_type': 'twiml',
            'twiml': twiml,
        })

    def test_render_application_destination(self):
        app = self._make_application(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Response><Say>Hello</Say></Response>')
        number = self.env['connect.voiceml.number'].create({
            'phone_number': '+18005551234',
            'destination': 'application',
            'application': app.id,
        })
        xml = number.render()
        self.assertIn('<Say>Hello</Say>', xml)

    def test_route_call_finds_number(self):
        app = self._make_application(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Response><Hangup /></Response>')
        self.env['connect.voiceml.number'].create({
            'phone_number': '+18005551234',
            'destination': 'application',
            'application': app.id,
        })
        # route_call feeds connect.call._on_call_status_voiceml, which expects the full
        # inbound webhook parameter set, not just the called number.
        xml = self.env['connect.voiceml.number']._route_call({
            'Called': '+18005551234',
            'CallSid': 'CA-test-1',
            'Caller': '+15551234567',
            'To': '+18005551234',
            'Direction': 'inbound',
            'CallStatus': 'ringing',
            'CallDuration': '0',
        })
        self.assertIn('<Hangup', xml)

    def test_unconfigured_number_greets(self):
        number = self.env['connect.voiceml.number'].create({
            'phone_number': '+18005559999',
        })
        xml = number.render()
        self.assertIn('Number not configured', xml)

    def test_domain_route_on_empty_recordset(self):
        # The /voiceml/webhook/domain controller calls this on an empty
        # recordset; an unknown To host must answer, not raise.
        xml = self.env['connect.voiceml.domain']._route_call({
            'To': 'sip:1001@unknown.example.com',
            'From': 'sip:alice@unknown.example.com',
        })
        self.assertIn('Number not found', xml)
