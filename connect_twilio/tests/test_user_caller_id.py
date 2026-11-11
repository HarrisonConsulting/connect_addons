# -*- coding: utf-8 -*-
"""Caller ID presented for calls a PBX user places.

An empty caller ID is not neutral: Twilio replaces it with an arbitrary
number of its own, which then shows on the callee's phone and lands in the
ledger as a bogus caller.
"""
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree

from odoo import Command, release
from odoo.exceptions import UserError, ValidationError

from odoo.tests import tagged

from .common import TwilioTestCommon


@tagged('at_install', '-post_install')
class TestUserCallerId(TwilioTestCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Settings = type(cls.env['connect.settings'])
        with patch.object(Settings, 'get_client', return_value=None):
            cls.domain = cls.env['connect.twilio.domain'].with_context(
                no_twilio_create=True).create({
                    'friendly_name': 'callerid-test',
                    'subdomain': 'calleridtest',
                })
        cls.pbx_user = cls._create_connect_user(
            'calleriduser', username='calleriduser', domain=cls.domain.id)
        cls.identity = cls.pbx_user.get_client_identity()
        taken = set(
            cls.env['connect.twilio.exten'].search([]).mapped('number'))
        cls.number = next(
            str(n) for n in range(7100, 8000) if str(n) not in taken)
        cls.env['connect.twilio.outgoing_callerid'].search(
            [('is_default', '=', True)]).is_default = False

    def _default_callerid(self, number='+15550009999'):
        # callerid_type 'number' is a Twilio-owned number: no validation
        # step, so it may be the default straight away.
        return self.env['connect.twilio.outgoing_callerid'].create({
            'friendly_name': 'Default',
            'number': number,
            'callerid_type': 'number',
            'is_default': True,
        })

    def test_extension_is_the_caller_id(self):
        self._default_callerid()
        self.env['connect.twilio.exten'].create({
            'number': self.number,
            'dst': 'connect.user,{}'.format(self.pbx_user.id),
        })

        self.assertEqual(self.pbx_user.twilio_caller_id(), self.number)

    def test_without_extension_falls_back_to_the_default_callerid(self):
        default = self._default_callerid()

        self.assertEqual(self.pbx_user.twilio_caller_id(), default.number)

    def test_own_outgoing_callerid_wins_over_the_default(self):
        self._default_callerid()
        own = self.env['connect.twilio.outgoing_callerid'].create({
            'friendly_name': 'Own',
            'number': '+15550008888',
            'callerid_type': 'number',
        })
        self.pbx_user.twilio_outgoing_callerid = own

        self.assertEqual(self.pbx_user.twilio_caller_id(), own.number)

    def test_last_resort_is_the_client_identity(self):
        """With no number to present, the caller is still not anonymous."""
        self.assertEqual(
            self.pbx_user.twilio_caller_id(),
            'client:{}'.format(self.identity),
        )

    def test_caller_id_is_never_empty_for_a_registered_user(self):
        """The fallback is what keeps Twilio from inventing a number."""
        self.assertTrue(self.pbx_user.twilio_caller_id())

    def test_dialplan_caller_id_resolves_the_calling_user(self):
        """_get_caller_id maps the calling client URI to its own caller ID."""
        self._default_callerid()
        self.env['connect.twilio.exten'].create({
            'number': self.number,
            'dst': 'connect.user,{}'.format(self.pbx_user.id),
        })

        caller_id = self.env['connect.user']._get_caller_id(
            {'Caller': 'client:{}'.format(self.identity)}, {})

        self.assertEqual(caller_id, self.number)

    def test_unknown_caller_is_passed_through(self):
        caller_id = self.env['connect.user']._get_caller_id(
            {'Caller': '+19789814066'}, {})

        self.assertEqual(caller_id, '+19789814066')

    def test_client_identity_fallback_resolves_back_to_the_user(self):
        """The ledger has to read the fallback back as the calling user."""
        channel = self.env['connect.channel'].create({
            'sid': 'CAcalleridfallback',
            'caller': self.pbx_user.twilio_caller_id(),
        })

        self.assertEqual(channel.caller_number, self.pbx_user.username)

    def _render_client(self, caller):
        from twilio.twiml.voice_response import VoiceResponse

        response = VoiceResponse()
        self.pbx_user.render_client(response, {'Caller': caller}, {})
        return str(response)

    def test_web_phone_is_told_the_extension_not_the_e164_form(self):
        """Twilio hands the callee '+101'; the widget must show '101'."""
        exten = self.env['connect.twilio.exten'].create({
            'number': self.number,
            'dst': 'connect.user,{}'.format(self.pbx_user.id),
        })

        dialplan = self._render_client('client:{}'.format(self.identity))

        self.assertIn('callerId="{}"'.format(exten.number), dialplan)
        self.assertIn(
            '<Parameter name="From" value="{}"'.format(exten.number),
            dialplan,
        )

    def test_a_phone_number_reaches_the_web_phone_untouched(self):
        """Only a bare extension needs the hint -- E.164 arrives as it is."""
        dialplan = self._render_client('+19789814066')

        self.assertIn('callerId="+19789814066"', dialplan)
        self.assertNotIn('<Parameter name="From"', dialplan)

    def test_a_whatsapp_caller_reaches_the_web_phone_untouched(self):
        """The widget reads the whatsapp: prefix off Twilio's own From."""
        dialplan = self._render_client('whatsapp:+37360681783')

        self.assertIn('callerId="whatsapp:+37360681783"', dialplan)
        self.assertNotIn('<Parameter name="From"', dialplan)


    def _voice_choices(self):
        default = self._default_callerid()
        own = self.env['connect.twilio.outgoing_callerid'].create({
            'friendly_name': 'Assigned', 'number': '+15550008888',
            'callerid_type': 'number',
        })
        other = self.env['connect.twilio.outgoing_callerid'].create({
            'friendly_name': 'Other', 'number': '+15550007777',
            'callerid_type': 'number',
        })
        self.pbx_user.twilio_outgoing_callerid = own
        return own, default, other

    def test_choices_remain_scoped_under_webhook_sudo_and_assigned_first(self):
        own, default, other = self._voice_choices()
        self.assertEqual(self.pbx_user.sudo()._twilio_phone_caller_ids(),
                         [own.number, default.number])
        with self.assertRaises(ValidationError):
            self.pbx_user.sudo()._twilio_external_caller_id(other.number)
        self.assertEqual(self.pbx_user._twilio_external_caller_id(default.number), default.number)

    def test_admin_can_choose_all_but_sudo_does_not_grant_membership(self):
        own, default, other = self._voice_choices()
        field = 'group_ids' if release.version_info[0] >= 19 else 'groups_id'
        self.pbx_user.user.write({field: [Command.link(self.env.ref('connect.group_admin').id)]})
        choices = self.pbx_user.sudo()._twilio_phone_caller_ids()
        self.assertEqual(choices[0], own.number)
        self.assertIn(default.number, choices)
        self.assertIn(other.number, choices)
        self.assertEqual(len(choices), len(set(choices)))

    def test_domain_uses_allowed_choice_and_denies_forgery(self):
        own, default, other = self._voice_choices()
        request = {'Caller': 'client:' + self.identity, 'CallerId': default.number}
        twiml = ElementTree.fromstring(str(self.domain.originate_external_call(
            '+15550006666', request)))
        self.assertEqual(twiml.find('Dial').attrib['callerId'], default.number)
        request['CallerId'] = other.number
        denied = ElementTree.fromstring(str(self.domain.originate_external_call(
            '+15550006666', request)))
        self.assertIsNone(denied.find('Dial'))

    def test_server_originate_uses_choice_on_transport_and_channel(self):
        own, default, other = self._voice_choices()
        self.pbx_user.originate_provider = 'twilio'
        settings = self.env['connect.settings']
        fake = MagicMock()
        fake.calls.create.return_value.sid = 'CAselectedcallerid'
        with patch.object(type(settings), 'get_client', return_value=fake):
            settings.with_user(self.pbx_user.user).originate_call(
                '+15550006666', caller_id=default.number)
            args = fake.calls.create.call_args.kwargs
            self.assertEqual(args['from_'], default.number)
            self.assertEqual(ElementTree.fromstring(args['twiml']).find('Dial').attrib['callerId'], default.number)
            channel = self.env['connect.channel'].search([('sid', '=', 'CAselectedcallerid')])
            self.assertEqual(channel.caller, default.number)
            fake.calls.create.reset_mock()
            with self.assertRaises(ValidationError):
                settings.with_user(self.pbx_user.user).originate_call(
                    '+15550006666', caller_id=other.number)
            fake.calls.create.assert_not_called()

    def test_sdk_history_retains_validated_choice_and_caller_authority(self):
        own, default, other = self._voice_choices()
        params = {
            'CallSid': 'CAsdkcallerchoice', 'Caller': 'client:' + self.identity,
            'Called': '+15550006666', 'To': '+15550006666',
            'Direction': 'inbound', 'CallStatus': 'initiated', 'CallerId': default.number,
        }
        channel = self.env['connect.channel'].on_call_status(params)
        self.assertEqual(channel.caller, default.number)
        self.assertEqual(channel.caller_user, self.pbx_user.user)
        del params['CallerId']
        params['CallStatus'] = 'completed'
        channel = self.env['connect.channel'].on_call_status(params)
        self.assertEqual(channel.caller, default.number)
        self.assertEqual(channel.caller_user, self.pbx_user.user)

    def test_sdk_sip_destination_maps_selected_identity(self):
        own, default, other = self._voice_choices()
        params = {
            'CallSid': 'CAsipcallerchoice', 'Caller': 'sip:' + self.identity,
            'Called': 'sip:+15550006666@example.sip.twilio.com',
            'Direction': 'inbound', 'CallStatus': 'initiated', 'CallerId': default.number,
        }
        mapped = self.env['connect.channel']._map_twilio_params(params)
        self.assertEqual(mapped['caller'], default.number)
        self.assertEqual(mapped['caller_pbx_user_id'], self.pbx_user.id)

    def test_domain_rejects_forgery_before_routing_or_history(self):
        own, default, other = self._voice_choices()
        with patch.object(type(self.env['oduist.license']), 'check_license', return_value=True), \
                patch.object(type(self.env['connect.call']), 'on_call_status') as history:
            result = self.domain.route_call({
                'Caller': 'client:' + self.identity, 'CallerId': other.number,
                'To': '+15550006666',
            })
        self.assertIsNone(ElementTree.fromstring(str(result)).find('Dial'))
        history.assert_not_called()

    def test_sdk_without_requested_choice_records_the_actual_default(self):
        own, default, other = self._voice_choices()
        params = {
            'CallSid': 'CAsdkdefaultchoice', 'Caller': 'client:' + self.identity,
            'Called': '+15550006666', 'To': '+15550006666',
            'Direction': 'inbound', 'CallStatus': 'initiated',
        }
        channel = self.env['connect.channel'].on_call_status(params)
        twiml = ElementTree.fromstring(str(self.domain.originate_external_call(
            '+15550006666', params)))
        self.assertEqual(channel.caller, own.number)
        self.assertEqual(channel.caller, twiml.find('Dial').attrib['callerId'])
        self.assertEqual(channel.caller_user, self.pbx_user.user)

    def test_server_contact_country_precedes_user_country(self):
        self._voice_choices()
        self.pbx_user.originate_provider = 'twilio'
        self.pbx_user.user.partner_id.country_id = self.env.ref('base.us')
        partner = self.env['res.partner'].create({
            'name': 'British dial target', 'phone': '020 7946 0018',
            'country_id': self.env.ref('base.gb').id,
        })
        settings = self.env['connect.settings'].with_user(self.pbx_user.user)
        fake = MagicMock()
        fake.calls.create.return_value.sid = 'CAcontactcountry'
        with patch.object(type(settings), 'get_client', return_value=fake):
            settings.originate_call(partner.phone, res_model='res.partner', res_id=partner.id)
        dial = ElementTree.fromstring(fake.calls.create.call_args.kwargs['twiml']).find('Dial')
        self.assertEqual(dial.find('Number').text, '+442079460018')

    def test_server_originate_refuses_attached_extension_before_carrier_or_history(self):
        self._voice_choices()
        self.pbx_user.originate_provider = 'twilio'
        settings = self.env['connect.settings'].with_user(self.pbx_user.user)
        Channel = self.env['connect.channel']
        before = Channel.search_count([])
        with patch.object(type(settings), 'get_client') as client:
            with self.assertRaisesRegex(UserError, 'in-call keypad'):
                settings.originate_call('+12025550123x123')
        client.assert_not_called()
        self.assertEqual(Channel.search_count([]), before)

    def test_browser_callback_refuses_attached_extension_without_destination_dial(self):
        result = str(self.domain.originate_external_call(
            '+12025550123x123', {'Caller': 'sip:unqualified@example.invalid'}))
        self.assertIn('in-call keypad', result)
        self.assertNotIn('<Dial', result)
        self.assertNotIn('<Number', result)
