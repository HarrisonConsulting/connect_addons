# -*- coding: utf-8 -*-
"""Audio-layer settings.

The DB-wide default voice, the hold music parked callers hear, and the
default source new audio rows are created with are audio library policy,
declared on top of the Twilio-shaped settings singleton.
"""

from odoo import fields, models

from .audio_referrer_mixin import (
    SELECTABLE_AUDIO_STATES,
    URL_PLAYABLE_AUDIO_SOURCES,
)
from .tts_mixin import DEFAULT_TWILIO_VOICE


class Settings(models.Model):
    _name = 'connect.settings'
    _inherit = ['connect.settings', 'connect.audio.referrer.mixin']

    _audio_reference_fields = ('park_hold_music_audio_id',)

    # default_twilio_voice is the DB-wide default for any connect.audio with
    # source=twilio_tts and use_default_voice=True. Kept as a Many2one on
    # connect.voice so the set of valid voices is data-driven (see
    # connect_hc_core/data/audio.xml) rather than hardcoded in a Selection.
    default_twilio_voice = fields.Many2one(
        'connect.voice', string='Default Twilio Voice',
        domain=[('provider', '=', 'twilio'), ('active', '=', True)],
        help='Default voice for Twilio <Say> output. Used by connect.audio '
             'rows with use_default_voice=True and by tts_mixin fallback '
             'system messages.')
    park_hold_music_audio_id = fields.Many2one(
        'connect.audio', ondelete='set null',
        domain=[
            ('state', 'in', SELECTABLE_AUDIO_STATES),
            ('source', 'in', URL_PLAYABLE_AUDIO_SOURCES),
        ],
        string='Park Hold Music',
        help="Audio played to parked callers. Leave empty for default classical "
             "music. Twilio's waitUrl only accepts a media URL, so TTS sources "
             "can't be used here — pick a Browser Recording, Internal "
             "Attachment, or External URL."
    )
    # Related surfacing of the picked audio's source so the settings form can
    # show a warning banner when an operator picks a TTS audio (which can't
    # play via waitUrl and will silently fall back to the default loop).
    park_hold_music_audio_id_source = fields.Selection(
        related='park_hold_music_audio_id.source', readonly=True)

    def get_default_audio_source(self):
        """Return (source, voice) tuple for newly-created connect.audio rows.

        Override in provider extensions (e.g. connect_elevenlabs) to switch the
        default to that provider when enabled. Base default is twilio_tts with
        no explicit voice (caller falls back to play_on()'s default).
        """
        return 'twilio_tts', self.env['connect.voice']

    def get_system_voice(self):
        """Return the Twilio voice external_id for <Say> fallbacks.

        Resolves settings.default_twilio_voice → external_id. Falls back to
        DEFAULT_TWILIO_VOICE (Polly.Joanna Standard) when unset so <Say>
        always has a concrete voice to render even on a fresh install before
        the operator picks one.
        """
        voice = self.sudo().search([], limit=1).default_twilio_voice
        return voice.external_id if voice else DEFAULT_TWILIO_VOICE
