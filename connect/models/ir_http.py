import hmac
import logging

from odoo import models
from odoo.http import request
from werkzeug.exceptions import Unauthorized

_logger = logging.getLogger(__name__)


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    @classmethod
    def _auth_method_connect_elevenlabs_tool(cls):
        """Reject an ElevenLabs tool call that does not carry the configured token."""
        token = request.httprequest.headers.get('x-elevenlabs-agent-token')
        expected = request.env['connect.settings'].sudo().get_param(
            'elevenlabs_agent_token'
        )
        if (
            not token
            or not expected
            or not hmac.compare_digest(token, expected)
        ):
            _logger.warning('ElevenLabs tool token rejected')
            raise Unauthorized(description='Invalid ElevenLabs tool token')
        cls._auth_method_public()
        request.session.can_save = False
        request.connect_elevenlabs_authenticated = True
