# -*- coding: utf-8 -*-

import logging
from odoo import models, fields, api

logger = logging.getLogger(__name__)

# Map of (text field on connect.user) -> (audio m2o field, root_var for Jinja).
# greeting_message: plain text; voicemail_prompt: Jinja with {{user.X}} legacy.
USER_AUDIO_FIELDS = (
    ('greeting_message', 'greeting_audio_id', None),
    ('voicemail_prompt', 'voicemail_audio_id', 'user'),
)


class ElevenLabsUser(models.Model):
    _name = 'connect.user'
    _inherit = ['connect.user', 'connect.audio.referrer.mixin']

    _audio_reference_fields = ('greeting_audio_id', 'voicemail_audio_id')
    _audio_reference_trigger_fields = ('active',)
    # Routing-wise: if active flips OR the m2o to an audio changes, the
    # reachability graph moves. user.callflow is a One2many of ring methods,
    # changes there fire writes on the child and don't always bubble up; we
    # accept a small lag until the parent is touched or "Refresh Reachability"
    # is clicked from the overview.
    _audio_reachability_fields = (
        'active', 'greeting_audio_id', 'voicemail_audio_id',
        'voicemail_enabled',
    )

    elevenlabs_enabled = fields.Boolean(compute='_get_elevenlabs_enabled')
    greeting_audio_id = fields.Many2one('connect.audio', ondelete='set null',
        string='Greeting Audio')
    voicemail_audio_id = fields.Many2one('connect.audio', ondelete='set null',
        string='Voicemail Prompt Audio')
    greeting_message_widget = fields.Html(
        related='greeting_audio_id.latest_utterance_id.preview_audio',
        string='Greeting Preview')
    voicemail_prompt_widget = fields.Html(
        related='voicemail_audio_id.latest_utterance_id.preview_audio',
        string='Voicemail Preview')

    def _get_elevenlabs_enabled(self):
        elevenlabs_enabled = self.env['connect.settings'].sudo().get_param('elevenlabs_enabled')
        for rec in self:
            rec.elevenlabs_enabled = elevenlabs_enabled

    # ------------------------------------------------------------------
    # Audio sync — runs on create/write (NOT @api.constrains; constraints
    # must be pure validators).
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            rec._sync_audio_fields()
        return records

    def write(self, vals):
        watched = {f for f, _, _ in USER_AUDIO_FIELDS} | {'voicemail_enabled'}
        result = super().write(vals)
        if watched & vals.keys():
            self._sync_audio_fields()
        return result

    def _sync_audio_fields(self):
        for rec in self:
            for text_field, audio_field, root_var in USER_AUDIO_FIELDS:
                rec._sync_one_audio(text_field, audio_field, root_var)

    def _sync_one_audio(self, text_field, audio_field, root_var):
        self.ensure_one()
        Audio = self.env['connect.audio'].sudo()
        text = self[text_field]
        audio = self[audio_field]

        if text_field == 'voicemail_prompt' and not self.voicemail_enabled:
            text = False

        auto_name = f'{self.username} {text_field}'

        # Operator manually selected a library audio — do not overwrite it.
        if audio and audio.name != auto_name:
            return

        if not text:
            if audio:
                audio.unlink()
            return

        source, voice = self._resolve_audio_source_voice()
        is_dynamic, static_text, model_id = self._template_args(text, root_var)
        vals = {
            'name': auto_name,
            'source': source,
            'voice_id': voice.id if voice else False,
            'static_text': static_text,
            'is_dynamic': is_dynamic,
            'model_id': model_id,
        }
        if audio:
            audio.write(vals)
        else:
            audio = Audio.create(vals)
            self[audio_field] = audio
        try:
            audio.with_delay()._precache(record=self)
        except Exception as e:
            logger.debug('queue_job pre-cache skipped: %s', e)

    def _resolve_audio_source_voice(self):
        return self.env['connect.settings'].sudo().get_default_audio_source()

    def _template_args(self, text, root_var):
        """Compute (is_dynamic, static_text, model_id) from a possibly-Jinja text.

        If the text is convertible to {token} syntax, return the converted form
        with is_dynamic=True. Otherwise pre-render via the legacy Jinja path
        (only voicemail uses this) and store as is_dynamic=False so the legacy
        text still plays.
        """
        Audio = self.env['connect.audio'].sudo()
        if not root_var:
            return False, text, False

        converted, ok = Audio.jinja_to_token(text, root_var=root_var)
        if ok and converted != text:
            model = self.env['ir.model'].sudo().search([('model', '=', self._name)], limit=1)
            return True, converted, model.id
        if ok:
            # No tokens at all — store as plain text. Still dynamic-eligible
            # so future edits work.
            return False, text, False
        # Unconvertible Jinja — fall back to pre-rendering.
        if root_var == 'user' and hasattr(self, 'render_voicemail_prompt'):
            return False, self.render_voicemail_prompt(), False
        return False, text, False

    # ------------------------------------------------------------------
    # Playback overrides
    # ------------------------------------------------------------------

    def get_greeting_message(self, response):
        if self.sudo().greeting_audio_id:
            try:
                self.sudo().greeting_audio_id.play_on(response, record=self)
            except Exception as e:
                logger.error('Audio render failed for user %s greeting: %s', self.id, e)

    def get_voicemail_prompt(self, response):
        if self.sudo().voicemail_audio_id:
            try:
                self.sudo().voicemail_audio_id.play_on(response, record=self)
            except Exception as e:
                logger.error('Audio render failed for user %s voicemail: %s', self.id, e)
