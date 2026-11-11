"""WhatsApp actions follow actor access and configured per-operation capability."""
from unittest.mock import patch
from odoo.exceptions import UserError, ValidationError
from odoo.tests import new_test_user, tagged
from .common import TwilioTestCommon


@tagged('post_install', '-at_install')
class TestWhatsappCapabilities(TwilioTestCommon):
    def setUp(self):
        super().setUp()
        self.env['connect.whatsapp_sender'].search([]).write({'no_sync': True, 'is_default': False})
        self.application = self.env['connect.twilio.twiml'].with_context(install_mode=True).create({
            'name': 'WhatsApp capabilities', 'sid': 'AP' + '4' * 32})
        with patch.object(type(self.env['connect.settings']), 'get_client', return_value=None):
            self.domain = self.env['connect.twilio.domain'].with_context(no_twilio_create=True).create({
                'friendly_name': 'WhatsApp capabilities', 'subdomain': 'whatsappcapabilities'})
        self.pbx_user = self._create_connect_user('whatsappcapabilityuser',
            username='whatsappcapabilityuser', domain=self.domain.id,
            client_enabled=True, originate_provider='twilio')
        self.actor = self.pbx_user.user
        self.dialer = self.env['connect.user'].with_user(self.actor)
        self.sender = self.env['connect.whatsapp_sender'].create({
            'number': '+15550008000', 'sid': 'XE' + '4' * 32,
            'status': 'ONLINE', 'is_default': True})

    def test_messaging_registration_does_not_imply_voice(self):
        self.assertFalse(self.pbx_user.whatsapp_sender_id)
        self.assertEqual(self.dialer.get_whatsapp_capabilities(), {'call': False, 'message': True})
        self.sender.voice_application = self.application
        self.assertEqual(self.dialer.get_whatsapp_capabilities(), {'call': True, 'message': True})
        self.pbx_user.with_context(no_twilio_create=True).client_enabled = False
        self.assertEqual(self.dialer.get_whatsapp_capabilities(), {'call': False, 'message': True})

    def test_absent_pending_unregistered_and_ignored_sender_hide(self):
        for values in ({'status': 'PENDING'}, {'status': 'OFFLINE'}, {'no_sync': True}, {'sid': False}):
            with self.subTest(values=values):
                self.sender.write({'status': 'ONLINE', 'no_sync': False, 'sid': 'XE' + '4' * 32})
                self.sender.write(values)
                self.assertEqual(self.dialer.get_whatsapp_capabilities(), {'call': False, 'message': False})
        self.sender.unlink()
        self.assertEqual(self.dialer.get_whatsapp_capabilities(), {'call': False, 'message': False})

    def test_sender_rule_and_unpermitted_actor_hide(self):
        outsider = new_test_user(self.env, login='whatsappoutsider', groups='base.group_user')
        self.assertEqual(self.dialer.with_user(outsider).get_whatsapp_capabilities(),
                         {'call': False, 'message': False})
        self.env['ir.rule'].create({
            'name': 'WhatsApp sender hidden',
            'model_id': self.env['ir.model']._get_id('connect.whatsapp_sender'),
            'domain_force': repr([('id', '!=', self.sender.id)]),
        })
        self.assertEqual(self.dialer.get_whatsapp_capabilities(), {'call': False, 'message': False})

    def test_stale_call_click_refuses_before_client_or_history(self):
        self.sender.voice_application = self.application
        self.assertTrue(self.dialer.get_whatsapp_capabilities()['call'])
        self.sender.status = 'OFFLINE'
        settings = self.env['connect.settings'].with_user(self.actor)
        before = self.env['connect.channel'].search_count([])
        with patch.object(type(settings), 'get_client') as client:
            with self.assertRaises(ValidationError):
                settings.originate_call('+15550008001', whatsapp_call=True)
            client.assert_not_called()
        self.assertEqual(self.env['connect.channel'].search_count([]), before)

    def test_stale_message_and_direct_voice_callback_refuse(self):
        self.assertTrue(self.dialer.get_whatsapp_capabilities()['message'])
        self.sender.status = 'OFFLINE'
        settings = self.env['connect.settings']
        with patch.object(type(settings), 'get_client') as client:
            with self.assertRaises(ValidationError):
                self.sender.with_user(self.actor).send_whatsapp('+15550008001', 'test')
            client.assert_not_called()
        twiml = str(self.domain.originate_whatsapp_call('+15550008001', {
            'Caller': 'client:whatsappcapabilityuser@whatsappcapabilities'}))
        self.assertNotIn('<Dial', twiml)
        self.assertIn('not available', twiml)

    def test_unknown_voice_route_preserves_messaging(self):
        self.sender.voice_application = self.application
        with patch.object(type(self.env['connect.settings']), '_get_originate_provider',
                          side_effect=UserError('Choose a voice service')):
            self.assertEqual(self.dialer.get_whatsapp_capabilities(), {'call': False, 'message': True})

    def test_messaging_user_needs_no_voice_account(self):
        person = new_test_user(self.env, login='whatsappmessageonly',
                               groups='base.group_user,connect.group_user')
        self.assertEqual(self.dialer.with_user(person).get_whatsapp_capabilities(),
                         {'call': False, 'message': True})
