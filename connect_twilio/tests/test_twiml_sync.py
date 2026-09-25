"""Module data updates preserve local TwiML records without remote writes."""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestTwimlSync(TransactionCase):

    def setUp(self):
        super().setUp()
        self.app = self.env['connect.twilio.twiml'].with_context(install_mode=True).create({
            'name': 'Local TwiML sync fixture',
            'sid': 'AP_local_sync_fixture',
        })

    def test_module_data_update_stays_local(self):
        """Updating seeded data never acquires a provider client or changes its SID."""
        Settings = type(self.env['connect.settings'])
        with patch.object(Settings, 'get_client') as client:
            self.app.write({'name': 'Updated seed app'})
        client.assert_not_called()
        self.assertEqual(self.app.name, 'Updated seed app')
        self.assertEqual(self.app.sid, 'AP_local_sync_fixture')

    def test_interactive_update_keeps_automatic_sync(self):
        """An ordinary edit still updates the remote application when sync is enabled."""
        Settings = type(self.env['connect.settings'])
        with (
            patch.object(Settings, 'get_param', return_value=True),
            patch.object(Settings, 'get_client') as client,
            patch.object(type(self.app), 'update_twilio_app') as update,
        ):
            self.app.with_context(install_mode=False).write({'name': 'Interactive edit'})
        client.assert_called_once()
        update.assert_called_once_with(client.return_value)
        self.assertEqual(self.app.name, 'Interactive edit')
