# -*- coding: utf-8 -*-

import hashlib
import hmac
import logging
from markupsafe import escape
from odoo import fields, models, api
from odoo.exceptions import ValidationError
from odoo.models import UniqueIndex

logger = logging.getLogger(__name__)


class AudioUtterance(models.Model):
    """Provider-blind cache row: one rendered binary per (audio, voice, text).

    Twilio TTS rows have file=NULL because <Say> is rendered live by Twilio.
    Callers branch on `if utterance.file: response.play(url) else response.say(rendered_text)`.
    """
    _name = 'connect.audio.utterance'
    _description = 'Audio Utterance'
    _order = 'generated_on desc'

    audio_id = fields.Many2one('connect.audio', required=True, ondelete='cascade', index=True)
    voice_id = fields.Many2one('connect.voice', ondelete='restrict')
    rendered_text = fields.Text(help='The fully resolved text after dynamic substitution.')
    text_hash = fields.Char(required=True, index=True,
        help='sha256 of rendered_text. Used for cache lookup.')
    params = fields.Char(help='JSON of provider-specific render params (model, stability, etc.). '
                              'Empty for renderers that have no tunable parameters.')
    params_hash = fields.Char(required=True, default='',
        help='sha256 of canonical params JSON. Empty when no params. Part of the cache key '
             'so utterances generated with different provider settings do not collide.')
    file = fields.Binary(attachment=True,
        help='Rendered audio bytes. Null for source=twilio_tts (live <Say>).')
    filename = fields.Char(
        help='File name for the rendered payload. For external_url utterances '
             'this field holds the full normalised URL that Twilio fetches.')
    mimetype = fields.Char(default='audio/mpeg',
        help='MIME type of the rendered payload, served verbatim by the '
             'signed-URL controller. Must be a Twilio-playable type.')
    source_used = fields.Char(help='Audit: which renderer produced this utterance.')
    generated_on = fields.Datetime(default=fields.Datetime.now, readonly=True)
    served_on = fields.Datetime(readonly=True,
        help='When this utterance was most recently fetched by Twilio from '
             'the signed-URL endpoint. Best-effort (a cache hit on the '
             'Twilio side may play without fetching again), but a reliable '
             'lower bound on last-played time.')
    served_count = fields.Integer(default=0, readonly=True,
        help='Number of times the signed-URL endpoint has served this '
             'utterance. Cheap popularity metric.')
    preview_audio = fields.Html(compute='_compute_preview_audio',
        string='Preview', sanitize=False)

    # Two partial indexes rather than one UNIQUE(...): voice_id is NULL for
    # every record-source utterance, and PostgreSQL lets NULLs collide freely
    # in a plain UNIQUE, so the cache key that matters most went unguarded.
    _unique_audio_voice_text_params = UniqueIndex(
        '(audio_id, voice_id, text_hash, params_hash) WHERE voice_id IS NOT NULL',
        'An utterance with this audio/voice/text/params already exists.',
    )
    _unique_audio_text_params_no_voice = UniqueIndex(
        '(audio_id, text_hash, params_hash) WHERE voice_id IS NULL',
        'An utterance with this audio/text/params already exists.',
    )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if rec.file and rec.mimetype:
                attachment = self.env['ir.attachment'].search([
                    ('res_model', '=', self._name),
                    ('res_field', '=', 'file'),
                    ('res_id', '=', rec.id),
                ], limit=1)
                if attachment and attachment.mimetype != rec.mimetype:
                    attachment.mimetype = rec.mimetype
        return records

    def _mark_served(self):
        """Best-effort update: records this utterance was just fetched.

        Called from the signed-URL controller on each serve. Failure must
        never break the serve, so the caller wraps this in try/except.
        Uses SQL to avoid firing recomputes on every Twilio fetch — the
        served_on / served_count columns aren't part of any compute graph.
        """
        self.ensure_one()
        now = fields.Datetime.now()
        self.env.cr.execute(
            'UPDATE connect_audio_utterance '
            '   SET served_on = %s, served_count = served_count + 1 '
            ' WHERE id = %s',
            (now, self.id),
        )

    @staticmethod
    def hash_text(text):
        return hashlib.sha256((text or '').encode('utf-8')).hexdigest()

    @staticmethod
    def hash_params(params):
        """Stable hash of a provider params dict. Empty dict / None → ''."""
        if not params:
            return ''
        import json
        canonical = json.dumps(params, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(canonical.encode('utf-8')).hexdigest()

    def get_path(self):
        """Internal preview path (authenticated, used by backend widgets).

        The filename segment is user-controllable (set by operators on
        upload), so callers that embed this path in HTML must escape the
        result — see _compute_preview_audio. For direct web-controller
        use the path round-trips through Odoo's routing where the
        filename is just informational.
        """
        self.ensure_one()
        return f'/web/content/{self._name}/{self.id}/file/{self.filename or ""}'

    def get_public_path(self):
        """Public, signed path for Twilio's media servers.

        Twilio fetches <Play> URLs anonymously — no Odoo session, no cookies.
        We sign with an HMAC of (id, create_date) so the URL is unguessable
        AND stable across regenerations. Stability matters because Twilio's
        media edge can fetch the URL mid-call after a queue_job regenerates
        the utterance; signing over write_date would invalidate the URL an
        edge is currently pulling, causing 403 mid-call. Replay risk from
        the stable token is mitigated by Cache-Control: private, no-store
        on dynamic utterances (see controllers/audio.py).
        """
        self.ensure_one()
        return f'/connect/audio/utterance/{self.id}?t={self._sign_token()}'

    def _sign_token(self):
        self.ensure_one()
        secret = self.env['ir.config_parameter'].sudo().get_param('database.secret') or ''
        # create_date is immutable — regens bump write_date but the token
        # stays stable, so Twilio's in-flight fetch doesn't 403 mid-call.
        ts = (self.create_date or fields.Datetime.now()).isoformat()
        msg = f'{self.id}:{ts}'.encode('utf-8')
        return hmac.new(secret.encode('utf-8'), msg, hashlib.sha256).hexdigest()

    def get_url(self):
        """Absolute URL for Twilio <Play>. Uses the signed public path."""
        self.ensure_one()
        api_url = (self.env['connect.settings'].sudo().get_param('api_url') or '').rstrip('/')
        if not api_url:
            raise ValidationError(
                'connect.settings.api_url is empty; cannot build a public '
                'audio URL for Twilio <Play>. Configure the API URL first.')
        return f'{api_url}{self.get_public_path()}'

    @api.depends('file', 'filename')
    def _compute_preview_audio(self):
        # Escape the path: `filename` ends up in an HTML attribute and is
        # operator-controllable (arbitrary upload filename), so unescaped
        # interpolation is a stored-XSS foot-gun even though the field is
        # rendered with sanitize=False for the <audio> element itself.
        for rec in self:
            if rec.file:
                src = escape(rec.get_path())
                rec.preview_audio = (
                    f'<audio controls preload="auto"><source src="{src}"/></audio>'
                )
            else:
                rec.preview_audio = ''
