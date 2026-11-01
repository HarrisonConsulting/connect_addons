"""The ElevenLabs tool auth method binds public identity only for the configured token."""
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from werkzeug.exceptions import Unauthorized

from odoo.tests import TransactionCase, tagged

from ..models import ir_http


@tagged('post_install', '-at_install')
class TestElevenLabsToolAuth(TransactionCase):

    def test_only_matching_configured_token_binds_identity(self):
        """Missing, unconfigured and forged tokens never reach the tool controllers."""
        for supplied, expected, allowed in (
            ('', 'configured-secret', False),
            ('configured-secret', False, False),
            ('forged', 'configured-secret', False),
            ('configured-secret', 'configured-secret', True),
        ):
            with self.subTest(supplied=supplied, configured=bool(expected)):
                incoming = SimpleNamespace(
                    env=self.env,
                    httprequest=SimpleNamespace(
                        headers={'x-elevenlabs-agent-token': supplied}),
                    session=SimpleNamespace(can_save=True),
                )
                logs = nullcontext() if allowed else self.assertLogs(
                    'odoo.addons.connect.models.ir_http', level='WARNING')
                with patch.object(ir_http, 'request', incoming), \
                     patch.object(type(self.env['connect.settings']), 'get_param',
                                  return_value=expected), \
                     patch.object(ir_http.IrHttp, '_auth_method_public') as bind, \
                     logs:
                    if allowed:
                        ir_http.IrHttp._auth_method_connect_elevenlabs_tool()
                    else:
                        with self.assertRaises(Unauthorized):
                            ir_http.IrHttp._auth_method_connect_elevenlabs_tool()
                self.assertEqual(bind.call_count, int(allowed))
                self.assertEqual(incoming.session.can_save, not allowed)
                self.assertEqual(
                    getattr(incoming, 'connect_elevenlabs_authenticated', False), allowed)
