# -*- coding: utf-8 -*-
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestNumberRouting(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Settings = self.env['connect.settings']
        self.settings = self.Settings.search([], limit=1)
        if not self.settings:
            self.settings = self.Settings.with_context(no_constrains=True).create({})
        self.Application = self.env['connect.voicetel.application']

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
        number = self.env['connect.voicetel.number'].create({
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
        self.env['connect.voicetel.number'].create({
            'phone_number': '+18005551234',
            'destination': 'application',
            'application': app.id,
        })
        xml = self.env['connect.voicetel.number'].route_call({
            'Called': '+18005551234',
            'CallSid': 'CAtest1',
            'CallStatus': 'completed',
            'Direction': 'inbound',
        })
        self.assertIn('<Hangup', xml)

    def test_unconfigured_number_greets(self):
        number = self.env['connect.voicetel.number'].create({
            'phone_number': '+18005559999',
        })
        xml = number.render()
        self.assertIn('Number not configured', xml)
