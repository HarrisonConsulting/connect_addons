# -*- coding: utf-8 -*-
"""The Send SMS dialog offers one From line, and only a line that can send."""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSmsComposerFrom(TransactionCase):

    def test_from_line_is_drawn_once(self):
        arch = self.env['sms.composer'].get_view(view_type='form')['arch']
        count = arch.count('name="outgoing_callerid"') + arch.count("name='outgoing_callerid'")
        self.assertEqual(count, 1)

    def test_a_line_the_provider_cannot_send_is_replaced(self):
        Composer = type(self.env['sms.composer'])
        allowed = [('+15745646338', '+15745646338')]
        with patch.object(Composer, '_list_all_numbers', return_value=allowed), \
                patch.object(Composer, '_default_outgoing_callerid', return_value='+15745646338'):
            values = self.env['sms.composer'].with_context(
                default_outgoing_callerid='+15743383149',
            ).default_get(['outgoing_callerid'])
        self.assertEqual(values['outgoing_callerid'], '+15745646338')

    def test_send_drops_a_stored_line_the_provider_cannot_use(self):
        Composer = type(self.env['sms.composer'])
        with patch.object(Composer, '_list_all_numbers', return_value=[('+15743383149', '+15743383149')]):
            composer = self.env['sms.composer'].create({
                'body': 'Did you get this message?',
                'composition_mode': 'numbers',
                'outgoing_callerid': '+15743383149',
            })
        with patch.object(Composer, '_list_all_numbers', return_value=[('+15745646338', '+15745646338')]), \
                patch.object(Composer, '_default_outgoing_callerid', return_value='+15745646338'):
            self.assertEqual(composer._outgoing_callerid_to_send(), '+15745646338')

    def test_send_passes_the_line_the_provider_can_use(self):
        Composer = type(self.env['sms.composer'])
        seen = {}

        def send(model, recipient, body, res_id, res_model, outgoing_callerid):
            seen['outgoing'] = outgoing_callerid

        with patch.object(Composer, '_list_all_numbers', return_value=[('+15745646338', '+15745646338')]):
            composer = self.env['sms.composer'].create({
                'body': 'Did you get this message?',
                'composition_mode': 'numbers',
                'numbers': '+12704733663',
                'outgoing_callerid': '+15745646338',
            })
        with patch.object(type(self.env['connect.message']), 'send', send):
            composer._action_send_sms()
        self.assertEqual(seen['outgoing'], '+15745646338')
