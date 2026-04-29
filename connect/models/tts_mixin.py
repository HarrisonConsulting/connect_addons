# -*- coding: utf-8 -*-

import logging
from odoo import models, api

logger = logging.getLogger(__name__)

# Default text for system messages when no connect.audio with system_key exists.
# Keep in sync with connect/data/audio.xml seeds.
SYSTEM_MESSAGES = {
    'system.transfer': 'Transfer',
    'system.connecting': 'Connecting...',
    'system.dnd': 'Do not disturb is enabled. Please leave a message.',
    'error.no_callerid': 'You must configure a default number for caller ID!',
    'error.callflow_empty': 'This callflow has no actions! Goodbye!',
    'error.call_failed': 'Sorry, I could not connect your call. Goodbye!',
    'error.no_extension': 'Extension not configured!',
    'error.choice_error': 'Choice application error, please contact technical support!',
    # TwiML audio() fallback messages — played when a reference can't be
    # served normally. Keep in sync with connect/data/audio.xml.
    'fallback.archived': 'This message is temporarily unavailable. Please hold.',
    'fallback.unresolved': 'A configuration error occurred. Please contact support.',
    # Defined in connect_addons_ee/connect_callout/data/audio.xml.
    'error.no_contact': 'No contact found for this callout. Goodbye!',
}

# Twilio's Standard Polly default. Standard Polly voices are free on all
# sub-accounts; Neural voices (Polly.*-Neural) incur per-character charges
# and aren't enabled on every sub-account by default. Legacy 'alice' is no
# longer guaranteed to work on new sub-accounts.
DEFAULT_TWILIO_VOICE = 'Polly.Joanna'


class TTSMixin(models.AbstractModel):
    """TTS abstraction. All speech goes through connect.audio so source dispatch,
    voice selection, dynamic rendering, and utterance caching live in one place.
    """
    _name = 'connect.tts.mixin'
    _description = 'TTS Abstraction Layer'

    @api.model
    def tts_say(self, response, message, message_key=None, language=None, voice=None, record=None):
        """Speak `message` via the configured pipeline.

        If message_key resolves to a connect.audio (system message), use it.
        Otherwise emit a Twilio <Say> with the caller-supplied (or settings-default)
        language and voice — this preserves per-callflow voice/language.
        """
        Audio = self.env['connect.audio'].sudo()

        if message_key:
            audio = Audio.search([('system_key', '=', message_key)], limit=1)
            if audio:
                try:
                    audio.play_on(response, record=record)
                    return
                except Exception as e:
                    logger.error('Audio render failed for system_key=%s: %s', message_key, e)

        settings = self.env['connect.settings'].sudo()
        # Caller-supplied kwargs win; fall back to settings; final fallback is Polly.
        language = language or settings.get_param('default_tts_language') or 'en-US'
        voice = voice or settings.get_param('default_tts_voice') or DEFAULT_TWILIO_VOICE
        response.say(message, language=language, voice=voice)

    @api.model
    def tts_system_message(self, response, message_key, language=None, voice=None, record=None):
        """Speak a system message by key. Falls back to SYSTEM_MESSAGES text via Twilio <Say>."""
        message = SYSTEM_MESSAGES.get(message_key, message_key)
        self.tts_say(response, message, message_key=message_key,
                     language=language, voice=voice, record=record)
