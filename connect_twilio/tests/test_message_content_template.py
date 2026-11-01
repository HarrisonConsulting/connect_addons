"""Seed loading preserves provider-approved content and its identity."""
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestTemplateDataLoad(TransactionCase):

    def _template(self, status):
        return self.env['connect.message_content_template'].with_context(install_mode=True).create({
            'friendly_name': 'module_seed_fixture',
            'language': self.env.ref('base.lang_en').id,
            'content_type': 'twilio/text',
            'body': 'Existing provider content',
            'status': status,
            'sid': 'HX_local_seed_fixture',
        })

    def test_submitted_seed_content_is_preserved(self):
        """An install-mode reload keeps the submitted payload, approval and provider SID."""
        template = self._template('approved')
        template._load_records_write({'body': 'New module default', 'friendly_name': 'new_default'})
        self.assertEqual(template.body, 'Existing provider content')
        self.assertEqual(template.friendly_name, 'module_seed_fixture')
        self.assertEqual(template.status, 'approved')
        self.assertEqual(template.sid, 'HX_local_seed_fixture')

    def test_unsubmitted_seed_content_can_update(self):
        """An editable template accepts the current module defaults."""
        template = self._template('unsubmitted')
        template._load_records_write({'body': 'New module default'})
        self.assertEqual(template.body, 'New module default')

    def test_ordinary_edits_keep_approval_guard(self):
        """Module loading does not weaken the guard on ordinary approved-content edits."""
        template = self._template('approved').with_context(install_mode=False)
        with self.assertRaises(ValidationError):
            template.write({'body': 'Unapproved edit'})
        self.assertEqual(template.body, 'Existing provider content')
