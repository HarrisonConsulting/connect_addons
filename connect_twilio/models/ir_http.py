"""Signature-authenticated boundaries for Twilio callback routes."""

from odoo import models
from odoo.http import request
from werkzeug.exceptions import Unauthorized

from odoo.addons.connect.tools import validate_twilio_request


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    @classmethod
    def _authenticate_twilio(cls, *, region):
        settings = request.env['connect.settings'].sudo()
        # Twilio signs the query string in the URL and only POST fields as data.
        if not validate_twilio_request(
            settings, request.httprequest, request.httprequest.form, region=region,
        ):
            raise Unauthorized(description='Invalid Twilio request signature')
        # Precedent: /mnt/19/odoo/odoo/addons/base/models/ir_http.py
        cls._auth_method_public()
        request.session.can_save = False
        request.connect_twilio_authenticated = True

    @classmethod
    def _auth_method_connect_twilio_voice(cls):
        cls._authenticate_twilio(region=True)

    @classmethod
    def _auth_method_connect_twilio_account(cls):
        cls._authenticate_twilio(region=False)
