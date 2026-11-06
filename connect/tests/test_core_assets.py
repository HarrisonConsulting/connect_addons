# -*- coding: utf-8 -*-
"""Core ships the assets a phone needs, with no provider module installed."""
from odoo.tests import TransactionCase, tagged
from odoo.tools.misc import file_path

CORE_ASSETS = (
    'connect/static/src/icomoon/style.css',
    'connect/static/src/services/actions/actions.js',
)
MAIL_FOLDER = '/static/src/services/mail/'


@tagged('post_install', '-at_install')
class TestCoreAssets(TransactionCase):

    def _backend_paths(self):
        paths = self.env['ir.asset']._get_asset_paths('web.assets_backend', {})
        return [path.lstrip('/') for path, *_rest in paths]

    def test_core_bundles_each_asset_once(self):
        paths = self._backend_paths()
        for asset in CORE_ASSETS:
            self.assertEqual(paths.count(asset), 1, asset)

    def test_twilio_no_longer_bundles_a_copy(self):
        paths = self._backend_paths()
        for folder in ('connect_twilio/static/src/icomoon/', 'connect_twilio/static/src/services/actions/'):
            self.assertFalse([path for path in paths if path.startswith(folder)], folder)

    def test_message_actions_come_only_from_twilio(self):
        """SMS and WhatsApp Reply open a wizard that only connect_twilio has."""
        bundled = [path for path in self._backend_paths() if MAIL_FOLDER in path]
        twilio = self.env['ir.module.module'].search_count(
            [('name', '=', 'connect_twilio'), ('state', '=', 'installed')])
        if twilio:
            self.assertIn('connect_twilio/static/src/services/mail/message_actions.js', bundled)
            self.assertFalse([path for path in bundled if path.startswith('connect/')], bundled)
        else:
            self.assertEqual(bundled, [])

    def test_core_has_no_unbundled_message_actions(self):
        with self.assertRaises(FileNotFoundError):
            file_path('connect/static/src/services/mail/message_actions.js')
