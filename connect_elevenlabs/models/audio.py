# -*- coding: utf-8 -*-

import base64
import logging
from odoo import fields, models, api
from odoo.exceptions import ValidationError

logger = logging.getLogger(__name__)


DEFAULT_MODEL_ID = 'eleven_flash_v2_5'
DEFAULT_STABILITY = 0.5
DEFAULT_SIMILARITY_BOOST = 0.75
DEFAULT_STYLE = 0.0


class Audio(models.Model):
    _inherit = 'connect.audio'

    # Register the selection value here so `elevenlabs_tts` only appears in the
    # source dropdown when this module is installed. Uninstalling flips
    # existing rows to the default (twilio_tts) rather than deleting audios.
    source = fields.Selection(
        selection_add=[('elevenlabs_tts', 'ElevenLabs TTS')],
        ondelete={'elevenlabs_tts': 'set default'},
    )

    @api.model
    def _dynamic_sources(self):
        return super()._dynamic_sources() | {'elevenlabs_tts'}

    def _renderers(self):
        renderers = super()._renderers()
        renderers['elevenlabs_tts'] = '_render_elevenlabs_tts'
        return renderers

    def _default_voice_for_source(self, source):
        if source == 'elevenlabs_tts':
            voice = self.env['connect.settings'].sudo().get_param('elevenlabs_voice')
            if voice and voice._name == 'connect.voice':
                return voice
        return super()._default_voice_for_source(source)

    @staticmethod
    def _build_voice_settings(params):
        """Translate our param dict into the SDK's VoiceSettings shape. Falls
        back to a plain dict if the SDK can't be imported (e.g. in unit tests)."""
        kwargs = {
            'stability': params['stability'],
            'similarity_boost': params['similarity_boost'],
            'style': params['style'],
            'use_speaker_boost': params['speaker_boost'],
        }
        try:
            from elevenlabs import VoiceSettings
            return VoiceSettings(**kwargs)
        except ImportError:
            return kwargs

    def _elevenlabs_settings_params(self):
        """Resolve synthesis params from settings, falling back to module defaults.

        Centralised so the cache key and the synthesis call can't drift.
        """
        settings = self.env['connect.settings'].sudo()
        return {
            'model_id': settings.get_param('elevenlabs_model_id') or DEFAULT_MODEL_ID,
            'stability': settings.get_param('elevenlabs_stability') or DEFAULT_STABILITY,
            'similarity_boost': (settings.get_param('elevenlabs_similarity_boost')
                                 or DEFAULT_SIMILARITY_BOOST),
            'style': settings.get_param('elevenlabs_style') or DEFAULT_STYLE,
            'speaker_boost': bool(settings.get_param('elevenlabs_speaker_boost')),
        }

    def _renderer_params(self, voice):
        if self.source == 'elevenlabs_tts':
            params = self._elevenlabs_settings_params()
            params['voice_external_id'] = voice.external_id if voice else None
            # Scope by locale so a multilingual voice reused across languages
            # can't collapse onto the same cache key as a sibling locale.
            if voice and voice.language:
                params['language'] = voice.language
            return params
        return super()._renderer_params(voice)

    def _render_elevenlabs_tts(self, rendered_text, voice):
        self.ensure_one()
        settings = self.env['connect.settings'].sudo()
        if not settings.get_param('elevenlabs_enabled'):
            raise ValidationError('ElevenLabs is not enabled in settings.')
        if not voice or voice.provider != 'elevenlabs':
            raise ValidationError(
                'source=elevenlabs_tts requires a voice with provider=elevenlabs '
                '(none configured on this audio or in settings.elevenlabs_voice).'
            )
        params = self._elevenlabs_settings_params()
        voice_settings = self._build_voice_settings(params)
        try:
            client = settings.get_elevenlabs_client()
            audio = client.text_to_speech.convert(
                text=rendered_text,
                voice_id=voice.external_id,
                model_id=params['model_id'],
                voice_settings=voice_settings,
            )
            data = b''.join(audio)
        except Exception as e:
            # Classify so ops can triage from logs: 401 is a config error (admin
            # must act), 429/5xx/network are transient (retry). Raising in all
            # cases keeps the utterance out of the cache so callers fall back
            # to <Say> and the next playback gets a fresh attempt.
            status = getattr(e, 'status_code', None)
            if status == 401:
                logger.error(
                    'ElevenLabs auth failed for audio %s — check API key: %s',
                    self.id, e)
                raise ValidationError('ElevenLabs authentication failed; check API key.')
            if status == 429:
                logger.warning(
                    'ElevenLabs rate-limit / quota exceeded for audio %s: %s',
                    self.id, e)
                raise ValidationError('ElevenLabs rate-limit / quota exceeded.')
            if status and 500 <= status < 600:
                logger.warning(
                    'ElevenLabs server error %s for audio %s: %s',
                    status, self.id, e)
                raise ValidationError(f'ElevenLabs service error ({status}).')
            logger.warning('ElevenLabs TTS failed for audio %s: %s', self.id, e)
            raise ValidationError(f'ElevenLabs error: {e}')

        import uuid
        return {
            'file': base64.b64encode(data).decode('utf-8'),
            'filename': f'{uuid.uuid4().hex}.mp3',
            'mimetype': 'audio/mpeg',
        }


class Voice(models.Model):
    _inherit = 'connect.voice'

    # Register the provider value here so ElevenLabs only shows up in the
    # voice provider dropdown when this module is installed. Uninstall uses
    # 'set default' (→ twilio) rather than 'cascade' because utterances pin
    # voice_id with ondelete='restrict' — a cascade delete would refuse.
    # The resulting orphan voice rows are harmless and preserve history;
    # they can be cleaned up manually if the module stays uninstalled.
    provider = fields.Selection(
        selection_add=[('elevenlabs', 'ElevenLabs')],
        ondelete={'elevenlabs': 'set default'},
    )

    @api.model
    def get_voices(self):
        """Sync ElevenLabs voices into connect.voice (provider='elevenlabs').

        Adds new ones, removes ones missing upstream. Idempotent.
        """
        client = self.env['connect.settings'].get_elevenlabs_client()
        response = client.voices.get_all()
        upstream_ids = {v.voice_id for v in response.voices}
        existing = self.search([('provider', '=', 'elevenlabs')])
        existing_ids = set(existing.mapped('external_id'))

        for v in response.voices:
            if v.voice_id in existing_ids:
                continue
            self.create({
                'name': v.name,
                'provider': 'elevenlabs',
                'external_id': v.voice_id,
                'language': getattr(getattr(v, 'fine_tuning', None), 'language', None),
                'accent': (v.labels or {}).get('accent'),
                'age': (v.labels or {}).get('age'),
                'gender': (v.labels or {}).get('gender'),
                'preview_url': v.preview_url,
                'description': (v.labels or {}).get('description'),
            })
            logger.info('Added ElevenLabs voice: %s id=%s', v.name, v.voice_id)

        # Archive rather than unlink so existing utterance rows (which hold
        # voice_id with ondelete='restrict') survive provider-side voice
        # deletions. An archived voice keeps its audit trail of which
        # utterances were rendered with it; new renders resolve a live voice.
        stale = existing.filtered(
            lambda r: r.external_id not in upstream_ids and r.active)
        if stale:
            stale.write({'active': False})

        settings = self.env['connect.settings'].sudo()
        if not settings.get_param('elevenlabs_voice'):
            last = self.search([('provider', '=', 'elevenlabs')], order='id desc', limit=1)
            if last:
                settings.set_param('elevenlabs_voice', last)
                logger.warning('Defaulted elevenlabs_voice to %s', last.external_id)
