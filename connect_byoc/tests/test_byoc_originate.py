# -*- coding: utf-8 -*-
"""Keypad dial-out through the shared originate hooks, with and without BYOC."""
from unittest.mock import patch
from xml.etree import ElementTree

from odoo.tests import tagged
from odoo.addons.connect_twilio.tests.common import TwilioTestCommon

REFUSED_ID = 'That caller ID is not available on your line.'
DEST = '+15550006666'


@tagged('post_install', '-at_install')
class TestByocOriginate(TwilioTestCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Settings = type(cls.env['connect.settings'])
        with patch.object(Settings, 'get_client', return_value=None):
            cls.domain = cls.env['connect.twilio.domain'].with_context(
                no_twilio_create=True).create({
                    'friendly_name': 'byoc-keypad-test',
                    'subdomain': 'byockeypadtest',
                })
        cls.pbx_user = cls._create_connect_user(
            'byockeypad', username='byockeypad', domain=cls.domain.id)
        cls.identity = cls.pbx_user.get_client_identity()
        CallerId = cls.env['connect.twilio.outgoing_callerid']
        CallerId.search([('is_default', '=', True)]).is_default = False
        cls.default = CallerId.create({
            'friendly_name': 'Default', 'number': '+15550009999',
            'callerid_type': 'number', 'is_default': True})
        cls.own = CallerId.create({
            'friendly_name': 'Own', 'number': '+15550008888',
            'callerid_type': 'number'})
        cls.other = CallerId.create({
            'friendly_name': 'Other', 'number': '+15550007777',
            'callerid_type': 'number'})
        cls.pbx_user.twilio_outgoing_callerid = cls.own
        Rule = cls.env['connect.outgoing_rule']
        Rule.search([]).is_enabled = False
        cls.plain_rule = Rule.create({'name': 'All', 'pattern': '+'})
        cls.byoc = cls.env['connect.byoc'].with_context(
            skip_twilio_sync=True).create({
                'friendly_name': 'Test BYOC', 'sid': 'BY' + '0' * 32})

    def _request(self, **extra):
        request = {'Caller': 'client:' + self.identity, 'To': DEST,
                   'CallSid': 'CAkeypad1'}
        request.update(extra)
        return request

    def _route_raw(self, request):
        with patch.object(type(self.env['oduist.license']), 'check_license',
                          return_value=True), \
                patch.object(type(self.env['connect.call']), 'on_call_status'):
            return str(self.env['connect.twilio.domain'].sudo().route_call(request))

    def _route(self, request):
        return ElementTree.fromstring(self._route_raw(request))

    def _byoc_rule(self, pattern='+1555', **kw):
        vals = {'name': 'BYOC ' + pattern, 'pattern': pattern, 'byoc': self.byoc.id}
        vals.update(kw)
        return self.env['connect.outgoing_rule'].create(vals)

    def test_keypad_call_without_callerid_dials_assigned_identity(self):
        dial = self._route(self._request()).find('Dial')
        self.assertIsNotNone(dial)
        self.assertEqual(dial.attrib['callerId'], self.own.number)
        self.assertIn('twilio/webhook/callaction#e=', dial.attrib['action'])
        number = dial.find('Number')
        self.assertEqual(number.text, DEST)
        self.assertNotIn('byoc', number.attrib)
        self.assertIn('callstatus#e=', number.attrib['statusCallback'])

    def test_keypad_call_with_allowed_callerid_presents_it(self):
        dial = self._route(self._request(CallerId=self.default.number)).find('Dial')
        self.assertEqual(dial.attrib['callerId'], self.default.number)

    def test_unknown_callerid_is_refused_in_words(self):
        request = self._request(CallerId=self.other.number)
        raw = self._route_raw(request)
        self.assertIn(REFUSED_ID, raw)
        self.assertNotIn('<Dial', raw)
        direct = str(self.domain.sudo().originate_external_call(DEST, request))
        self.assertIn(REFUSED_ID, direct)
        self.assertNotIn('<Dial', direct)

    def test_unresolved_caller_uses_system_default_and_refuses_callerid(self):
        request = self._request(Caller='client:nobody')
        dial = self._route(request).find('Dial')
        self.assertEqual(dial.attrib['callerId'], self.default.number)
        request['CallerId'] = self.default.number
        raw = self._route_raw(request)
        self.assertIn(REFUSED_ID, raw)
        self.assertNotIn('<Dial', raw)

    def test_recording_follows_settings_gate(self):
        Settings = type(self.env['connect.settings'])
        with patch.object(Settings, 'calls_are_recorded', return_value=True) as gate:
            dial = self._route(self._request()).find('Dial')
        self.assertEqual(dial.attrib['record'], 'record-from-answer')
        self.assertIn('recordingstatus#e=', dial.attrib['recordingStatusCallback'])
        self.assertEqual(gate.call_args.kwargs.get('user'), self.pbx_user)

    def test_byoc_rule_routes_on_the_carrier(self):
        self._byoc_rule(trim_leading_digits=1, add_prefix='9')
        dial = self._route(self._request()).find('Dial')
        number = dial.find('Number')
        self.assertEqual(number.text, '915550006666')
        self.assertEqual(number.attrib['byoc'], self.byoc.sid)
        self.assertIn('twilio/webhook/callaction', dial.attrib['action'])

    def test_byoc_default_callerid_cases(self):
        rule = self._byoc_rule()
        with self.subTest('a: byoc default when the user has none'):
            self.pbx_user.twilio_outgoing_callerid = False
            self.byoc.default_callerid = self.other
            dial = self._route(self._request()).find('Dial')
            self.assertEqual(dial.attrib['callerId'], self.other.number)
        with self.subTest('b: same country keeps the chosen caller ID'):
            dial = self._route(self._request(CallerId=self.default.number)).find('Dial')
            self.assertEqual(dial.attrib['callerId'], self.default.number)
        with self.subTest('c: other country takes the byoc default'):
            rule.is_enabled = False
            self._byoc_rule(pattern='+44')
            dial = self._route(self._request(
                To='+442079460018', CallerId=self.default.number)).find('Dial')
            self.assertEqual(dial.attrib['callerId'], self.other.number)
        with self.subTest('d: no byoc default, no assignment, no CallerId'):
            self.byoc.default_callerid = False
            dial = self._route(self._request(
                To='+442079460018')).find('Dial')
            self.assertEqual(dial.attrib['callerId'], self.default.number)
        with self.subTest('e: forged CallerId still refused'):
            raw = self._route_raw(self._request(
                To='+442079460018', CallerId=self.other.number))
            self.assertIn(REFUSED_ID, raw)
            self.assertNotIn('<Dial', raw)

    def test_no_rule_is_refused_in_words(self):
        self.env['connect.outgoing_rule'].search([]).is_enabled = False
        raw = self._route_raw(self._request())
        self.assertIn('No outgoing rule found for this destination.', raw)
        self.assertNotIn('<Dial', raw)

    def test_server_route_delegates_without_byoc(self):
        settings = self.env['connect.settings']
        twiml = settings.get_external_call_route(
            DEST, self.default.number, 'https://x/cb')
        self.assertIn('timeLimit=', twiml)
        self.assertNotIn('byoc=', twiml)
        self._byoc_rule(trim_leading_digits=1, add_prefix='9')
        twiml = settings.get_external_call_route(
            DEST, self.default.number, 'https://x/cb')
        self.assertIn('byoc="{}"'.format(self.byoc.sid), twiml)
        self.assertIn('915550006666', twiml)
        self.assertIn('timeLimit=', twiml)
