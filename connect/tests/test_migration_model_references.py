import importlib.util
from pathlib import Path

from odoo.tests import TransactionCase, tagged


def _migration_module(version, filename):
    path = Path(__file__).parents[1] / 'migrations' / version / filename
    spec = importlib.util.spec_from_file_location(
        'connect_migration_%s' % version.replace('.', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@tagged('post_install', '-at_install')
class TestMigrationModelReferences(TransactionCase):

    def test_moved_model_names_are_retargeted_exactly(self):
        """Stored model names follow the Twilio rename; other names stay untouched."""
        migration = _migration_module('19.0.4.4.16', 'post-migration.py')
        partner = self.env.user.partner_id
        moved, prefixed = self.env['ir.attachment'].create([
            {'name': 'moved.txt', 'res_model': 'res.partner', 'res_id': partner.id},
            {'name': 'prefixed.txt', 'res_model': 'res.partner', 'res_id': partner.id},
        ])
        message = partner.message_post(body='moved')
        # The old models are gone from the registry, so stale names are
        # written the way the pre-split rows hold them: directly.
        cr = self.env.cr
        cr.execute("UPDATE ir_attachment SET res_model = 'connect.twiml' WHERE id = %s", (moved.id,))
        cr.execute("UPDATE ir_attachment SET res_model = 'connect.twiml_extra' WHERE id = %s", (prefixed.id,))
        cr.execute("UPDATE mail_message SET model = 'connect.domain' WHERE id = %s", (message.id,))

        migration.rename_model_references(self.env.cr)
        self.env.invalidate_all()

        self.assertEqual(moved.res_model, 'connect.twilio.twiml')
        self.assertEqual(prefixed.res_model, 'connect.twiml_extra')
        self.assertEqual(message.model, 'connect.twilio.domain')
