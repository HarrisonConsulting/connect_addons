# -*- coding: utf-8 -*-
"""Audio-layer settings.

The DB-wide default voice, the hold music parked callers hear, and the
default source new audio rows are created with are audio library policy,
declared on top of the Twilio-shaped settings singleton.
"""

import json
import re

from odoo import api, fields, models

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
    # connect_pbx/data/audio.xml) rather than hardcoded in a Selection.
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
    last_reachability_refresh_on = fields.Datetime(
        readonly=True, string='Reachability Last Refreshed',
        help='Stamped by connect.audio._refresh_reachability on each BFS pass.')
    voicemail_max_length = fields.Integer(
        string='Voicemail Max Length', default=120,
        help='Maximum voicemail recording length in seconds.')
    voicemail_finish_key = fields.Selection(
        [('0', '0'), ('1', '1'), ('2', '2'), ('3', '3'), ('4', '4'),
         ('5', '5'), ('6', '6'), ('7', '7'), ('8', '8'), ('9', '9'),
         ('*', '*'), ('#', '#')],
        string='Voicemail Finish Key', default='#',
        help='Key that callers press to finish recording a voicemail.')
    park_slot_count = fields.Integer(
        string='Park Slot Count', default=9,
        help='Number of available call parking slots (1-99).')
    park_timeout = fields.Integer(
        string='Park Timeout', default=300,
        help='Seconds before a parked call times out and rings back the parker (0 = no timeout).')
    park_announcement_enabled = fields.Boolean(
        string='Park Announcement', default=False,
        help='Play slot number announcement when parking a call.')
    pronunciation_rules = fields.Text(
        string='Pronunciation Rules',
        help='JSON map of text to pronunciation substitutions.')

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

    @api.model
    def process_pronunciation(self, text):
        """Apply SSML pronunciation substitutions from settings JSON."""
        if not text:
            return text
        try:
            rules_json = self.sudo().get_param('pronunciation_rules')
            if not rules_json:
                return text
            rules = json.loads(rules_json)
            processed_text = text
            for original, pronunciation in rules.items():
                pattern = re.compile(re.escape(original), re.IGNORECASE)
                if pattern.search(processed_text):
                    safe = (pronunciation
                            .replace('&', '&amp;')
                            .replace('"', '&quot;')
                            .replace('<', '&lt;')
                            .replace('>', '&gt;'))

                    def replace_func(match, alias=safe):
                        return f'<sub alias="{alias}">{match.group(0)}</sub>'

                    processed_text = pattern.sub(replace_func, processed_text)
            return processed_text
        except Exception:
            return text
