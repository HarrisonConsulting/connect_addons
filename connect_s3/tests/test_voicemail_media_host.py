from unittest.mock import MagicMock, patch

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestVoicemailMediaHost(TransactionCase):
    def test_untrusted_host_is_never_fetched(self):
        settings = self.env['connect.settings'].search([], limit=1)
        if not settings:
            settings = self.env['connect.settings'].create({})
        settings.write({'recording_storage': 's3', 's3_bucket': 'test-recordings'})
        call = self.env['connect.call'].create({
            'voicemail_url': 'https://evil.example.invalid/vm.mp3',
            'voicemail_sid': 'RE_untrusted',
        })
        s3 = MagicMock()
        with patch.object(type(settings), 'get_s3_client', return_value=s3), \
                patch.object(type(settings), '_s3_object_verified', return_value=False), \
                patch.object(type(settings), '_is_trusted_media_url', return_value=False), \
                patch('odoo.addons.connect_s3.models.call.requests.get') as get:
            call._store_voicemail_as_attachment()
            call._migrate_voicemail_to_s3(settings)
        get.assert_not_called()
        s3.upload_fileobj.assert_not_called()
