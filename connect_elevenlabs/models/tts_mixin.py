# -*- coding: utf-8 -*-

import logging
from odoo import models, api

logger = logging.getLogger(__name__)


class ElevenLabsTTSMixin(models.AbstractModel):
    _inherit = 'connect.tts.mixin'

    @api.model
    def tts_say(self, response, message, message_key=None, language=None, voice=None):
        """
        Override to use ElevenLabs play() when enabled.
        Falls back to native say() when disabled or on error.
        """
        settings = self.env['connect.settings'].sudo()

        if not settings.get_param('elevenlabs_enabled'):
            return super().tts_say(response, message, message_key, language, voice)

        try:
            audio_url = self._elevenlabs_get_audio_url(message, message_key)
            if audio_url:
                response.play(audio_url)
                return
        except Exception as e:
            logger.error('ElevenLabs TTS error for message_key=%s: %s', message_key, e)

        # Fallback to native say()
        return super().tts_say(response, message, message_key, language, voice)

    @api.model
    def tts_system_message(self, response, message_key, language=None, voice=None):
        """
        Override to use pre-generated system message audio when available.
        """
        settings = self.env['connect.settings'].sudo()

        if not settings.get_param('elevenlabs_enabled'):
            return super().tts_system_message(response, message_key, language, voice)

        try:
            # Look up pre-generated system message
            system_msg = self.env['connect.elevenlabs_system_message'].sudo().search([
                ('message_key', '=', message_key)
            ], limit=1)

            if system_msg and system_msg.file:
                response.play(system_msg.get_file_url())
                return
        except Exception as e:
            logger.error('ElevenLabs system message error for key=%s: %s', message_key, e)

        # Fallback to parent implementation
        return super().tts_system_message(response, message_key, language, voice)

    @api.model
    def _elevenlabs_get_audio_url(self, message, message_key):
        """
        Get or generate audio URL for a message.
        Returns None if audio cannot be generated.
        """
        if not message:
            return None

        # For system messages with keys, try to find pre-generated audio
        if message_key:
            system_msg = self.env['connect.elevenlabs_system_message'].sudo().search([
                ('message_key', '=', message_key)
            ], limit=1)
            if system_msg and system_msg.file:
                return system_msg.get_file_url()

        # For dynamic messages, generate on-the-fly with caching
        file_model = self.env['connect.elevenlabs_file'].sudo()

        # Check if we already have this exact text cached
        existing = file_model.search([('text', '=', message)], limit=1)
        if existing and existing.file:
            return existing.get_file_url()

        # Generate new audio file
        new_file = file_model.create({'text': message})
        if new_file.file:
            return new_file.get_file_url()

        return None
