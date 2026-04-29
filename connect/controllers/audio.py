# -*- coding: utf-8 -*-

import base64
import hashlib
import hmac
import logging

from odoo import http
from odoo.http import request, Response
from werkzeug.exceptions import NotFound, Forbidden

logger = logging.getLogger(__name__)


class AudioController(http.Controller):
    """Public, signed endpoint for serving connect.audio.utterance binaries
    to Twilio's media servers.

    Twilio fetches <Play> URLs anonymously: there is no Odoo session, no
    cookies, no group membership. The webhook ACL on connect.audio.utterance
    is irrelevant here. We protect the binary with an HMAC over (id, write_date)
    using `database.secret` — guessing the URL requires the secret, and the
    URL invalidates whenever the binary regenerates.
    """

    @http.route('/connect/audio/utterance/<int:utterance_id>',
                type='http', auth='public', methods=['GET'], csrf=False)
    def serve_utterance(self, utterance_id, t=None, **kwargs):
        if not t:
            raise Forbidden('missing token')

        utterance = request.env['connect.audio.utterance'].sudo().browse(utterance_id).exists()
        if not utterance:
            raise NotFound()

        expected = utterance._sign_token()
        if not hmac.compare_digest(t, expected):
            raise Forbidden('bad token')

        if not utterance.file:
            raise NotFound('no binary; this utterance is text-only (Twilio <Say>)')

        # Best-effort play-tracking: a failure here must not break playback,
        # so we swallow exceptions rather than propagate to Twilio.
        try:
            utterance._mark_served()
        except Exception:
            logger.warning('mark_served failed for utterance %s', utterance.id,
                           exc_info=True)

        data = base64.b64decode(utterance.file)
        # Dynamic audios interpolate caller/record PII into the rendered bytes;
        # a shared proxy/CDN caching the Response could leak that PII across
        # callers. Static audios are safe to cache aggressively — they carry no
        # per-caller state.
        if utterance.audio_id.is_dynamic:
            cache_control = 'private, no-store'
        else:
            cache_control = 'public, max-age=86400'
        return Response(
            data,
            status=200,
            mimetype=utterance.mimetype or 'audio/mpeg',
            headers=[
                ('Content-Length', str(len(data))),
                ('Content-Disposition', f'inline; filename="{utterance.filename or "audio"}"'),
                ('Cache-Control', cache_control),
            ],
        )
