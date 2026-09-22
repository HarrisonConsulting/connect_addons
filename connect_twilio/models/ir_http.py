import logging

from odoo import models
from odoo.addons.connect.tools import validate_twilio_request
from odoo.http import request
from werkzeug.exceptions import Unauthorized

_logger = logging.getLogger(__name__)


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    @classmethod
    def _auth_twilio_request(cls):
        settings = request.env['connect.settings'].sudo()
        data = request.httprequest.form.to_dict(flat=True)
        if not validate_twilio_request(settings, request.httprequest, data):
            _logger.warning('Twilio signature rejected')
            raise Unauthorized(description='Invalid Twilio signature')
        cls._auth_method_public()
        request.session.can_save = False

    @classmethod
    def _auth_method_connect_twilio_voice(cls):
        cls._auth_twilio_request()

    @classmethod
    def _auth_method_connect_twilio_account(cls):
        cls._auth_twilio_request()
