# -*- coding: utf-8 -*-
"""Authentication boundaries for Connect machine-facing routes."""

import hmac
import json
import re

from odoo import models
from odoo.http import request
from werkzeug.exceptions import Unauthorized

from odoo.addons.connect.tools import validate_twilio_request


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    @classmethod
    def _authenticate_twilio(cls, region):
        settings = request.env['connect.settings'].sudo()
        if not validate_twilio_request(
            settings,
            request.httprequest,
            request.get_http_params(),
            region=region,
        ):
            raise Unauthorized(description='Invalid Twilio request signature')
        cls._auth_method_public()
        request.session.can_save = False
        request.connect_twilio_authenticated = True

    @classmethod
    def _auth_method_connect_twilio_voice(cls):
        cls._authenticate_twilio(region=True)

    @classmethod
    def _auth_method_connect_twilio_account(cls):
        cls._authenticate_twilio(region=False)

    @classmethod
    def _auth_method_connect_elevenlabs_tool(cls):
        supplied = request.httprequest.headers.get('x-elevenlabs-agent-token', '')
        expected = request.env['connect.settings'].sudo().get_param(
            'elevenlabs_agent_token'
        )
        if not supplied or not expected or not hmac.compare_digest(supplied, expected):
            raise Unauthorized(description='Invalid ElevenLabs agent token')
        cls._auth_method_public()
        request.session.can_save = False
        request.connect_elevenlabs_authenticated = True

    @classmethod
    def _auth_method_connect_transcript(cls):
        match = re.match(r'^/connect/transcript/(\d+)$', request.httprequest.path)
        recording_id = int(match.group(1)) if match else 0
        try:
            body = json.loads(request.httprequest.get_data() or b'{}')
        except (TypeError, ValueError):
            body = {}
        supplied = body.get('transcription_token') if isinstance(body, dict) else None
        recording = request.env['connect.recording'].sudo().search([
            ('id', '=', recording_id),
            ('transcription_token', '!=', False),
            ('transcription_token', '=', supplied),
        ], limit=1)
        if not supplied or not recording:
            raise Unauthorized(description='Invalid transcription token')
        cls._auth_method_public()
        request.session.can_save = False
        request.connect_recording = recording

    @classmethod
    def _auth_method_connect_health(cls):
        match = re.match(r'^/connect/health/([^/]+)/$', request.httprequest.path)
        supplied = match.group(1) if match else ''
        expected = request.env['connect.settings'].sudo().get_param('instance_uid')
        if not supplied or not expected or not hmac.compare_digest(supplied, expected):
            raise Unauthorized(description='Invalid Connect health token')
        cls._auth_method_public()
        request.session.can_save = False
