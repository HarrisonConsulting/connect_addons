# -*- coding: utf-8 -*-

import logging
from odoo import models, api

logger = logging.getLogger(__name__)

# System message defaults - can be overridden via ElevenLabs system message catalog
SYSTEM_MESSAGES = {
    'system.transfer': 'Transfer',
    'system.connecting': 'Connecting...',
    'error.no_callerid': 'You must configure a default number for caller ID!',
    'error.callflow_empty': 'This callflow has no actions! Goodbye!',
    'error.call_failed': 'Sorry, I could not connect your call. Goodbye!',
    'error.no_extension': 'Extension not configured!',
    'error.choice_error': 'Choice application error, please contact technical support!',
}


class TTSMixin(models.AbstractModel):
    _name = 'connect.tts.mixin'
    _description = 'TTS Abstraction Layer'

    @api.model
    def tts_say(self, response, message, message_key=None, language=None, voice=None):
        """
        Speak a message via TTS. Override in ElevenLabs module for play().

        Args:
            response: VoiceResponse, Gather, or similar TwiML object
            message: The text to speak
            message_key: Optional key for pre-generated audio lookup
            language: Language code (e.g., 'en-US')
            voice: Voice name (e.g., 'alice', 'man', 'woman')

        Returns:
            None (modifies response in-place)
        """
        settings = self.env['connect.settings'].sudo()
        language = language or settings.get_param('default_tts_language') or 'en-US'
        voice = voice or settings.get_param('default_tts_voice') or 'alice'
        response.say(message, language=language, voice=voice)

    @api.model
    def tts_system_message(self, response, message_key, language=None, voice=None):
        """
        Speak a system message by key.

        Args:
            response: VoiceResponse or similar TwiML object
            message_key: Key to look up in SYSTEM_MESSAGES
            language: Optional language override
            voice: Optional voice override
        """
        message = SYSTEM_MESSAGES.get(message_key, message_key)
        self.tts_say(response, message, message_key=message_key, language=language, voice=voice)
