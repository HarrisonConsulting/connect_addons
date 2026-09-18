# -*- coding: utf-8 -*-
"""Authentication boundary for the public audio media route."""

import hmac
import re

from odoo import models
from odoo.http import request
from werkzeug.exceptions import Unauthorized


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    @classmethod
    def _auth_method_connect_audio(cls):
        match = re.match(
            r'^/connect/audio/utterance/(\d+)$', request.httprequest.path
        )
        utterance_id = int(match.group(1)) if match else 0
        supplied = request.httprequest.args.get('t', '')
        utterance = request.env['connect.audio.utterance'].sudo().browse(
            utterance_id
        ).exists()
        expected = utterance._sign_token() if utterance else ''
        if not supplied or not expected or not hmac.compare_digest(supplied, expected):
            raise Unauthorized(description='Invalid Connect audio token')
        cls._auth_method_public()
        request.session.can_save = False
        request.connect_audio_utterance = utterance
