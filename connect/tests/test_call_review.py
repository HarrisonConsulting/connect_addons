"""Call review contracts apply to the fully composed installed form."""
from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCallReview(TransactionCase):

    def test_combined_form_starts_with_media_and_keeps_extension_anchors(self):
        arch = self.env.ref('connect.view_connect_call_form')._get_combined_arch()
        pages = arch.xpath('//sheet/notebook/page')
        self.assertEqual(pages[0].get('name'), 'recording')
        self.assertEqual(len(pages[0].xpath('./field[@name="transcript"]')), 1)
        self.assertEqual(len(arch.xpath('//page[@name="call"][@invisible="1"]')), 1)
        self.assertEqual(len(arch.xpath('//div[@name="button_box"]')), 1)
        for name in ('details', 'channels'):
            node = arch.xpath('//page[@name="%s"]' % name)[0]
            self.assertIn('base.group_no_one', node.get('groups', ''))
        # This catches a downstream inherit replacing the media condition with
        # transcript-only visibility, including modelnexus diarized transcripts.
        hidden = pages[0].get('invisible')
        values = {'recording_widget': '<audio/>', 'transcript': False,
                  'html_transcript': False, 'elevenlabs_recording_widget': False,
                  'elevenlabs_transcript': False}
        self.assertFalse(eval(hidden, {'__builtins__': {}}, values), hidden)
        values['recording_widget'] = False
        values['transcript'] = 'What was said'
        self.assertFalse(eval(hidden, {'__builtins__': {}}, values), hidden)
        values['transcript'] = False
        self.assertTrue(eval(hidden, {'__builtins__': {}}, values), hidden)

    def test_review_buttons_open_exact_relation_without_reassigning(self):
        partner = self.env['res.partner'].create({'name': 'Call review contact'})
        call = self.env['connect.call'].create({'partner': partner.id})
        for relation, model in [('partner', 'res.partner'), ('lead', 'crm.lead'),
                                ('ticket', 'helpdesk.ticket')]:
            if relation not in call._fields:
                continue
            record = partner if relation == 'partner' else self.env[model].create({
                'name': 'Call review connection'})
            call[relation] = record
            action = getattr(call, 'action_open_' + relation)()
            self.assertEqual((action['res_model'], action['res_id']), (model, record.id))
            self.assertEqual(call[relation], record)
            self.assertTrue(call['can_open_' + relation])
        call.partner = False
        self.assertFalse(call.action_open_partner())
        self.assertFalse(call.can_open_partner)

    def test_review_action_enforces_related_record_read_access(self):
        partner = self.env['res.partner'].create({'name': 'Protected review contact'})
        call = self.env['connect.call'].create({'partner': partner.id})
        with patch.object(type(partner), 'check_access', side_effect=AccessError('Denied')):
            with self.assertRaises(AccessError):
                call.action_open_partner()
        self.assertEqual(call.partner, partner)
