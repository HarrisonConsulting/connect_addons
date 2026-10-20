# -*- coding: utf-8 -*-
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestVoicemail(TransactionCase):

    def test_voicemail_record_has_callbacks(self):
        flow = self.env['connect.voiceml.callflow'].create({
            'name': 'VM flow',
            'voicemail_enabled': True,
            'voicemail_prompt': 'Leave a message',
        })
        api_url = self.env['connect.settings'].sudo().get_param('api_url')
        xml = flow.render()
        self.assertIn('<Record', xml)
        self.assertIn('voiceml/webhook/callaction', xml)
        self.assertIn('voiceml/webhook/vm_recordingstatus', xml)
        self.assertIn('recordingStatusCallbackEvent="completed"', xml)
        self.assertIn('method="POST"', xml)
        self.assertTrue(api_url)
