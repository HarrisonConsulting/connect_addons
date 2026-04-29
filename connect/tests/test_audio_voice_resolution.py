# -*- coding: utf-8 -*-
"""Tests for connect.audio voice resolution — the use_default_voice knob,
_resolve_voice fallback chain, resolved_voice_id compute, and settings.
default_twilio_voice wiring introduced in connect 1.14.0.
"""

from odoo.tests import tagged

from .common import ConnectTestCase


@tagged('post_install', '-at_install')
class TestAudioVoiceResolution(ConnectTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Voice = cls.env['connect.voice']
        cls.joanna = Voice.search([
            ('provider', '=', 'twilio'),
            ('external_id', '=', 'Polly.Joanna'),
        ], limit=1) or Voice.create({
            'name': 'Joanna',
            'provider': 'twilio',
            'external_id': 'Polly.Joanna',
        })
        cls.matthew = Voice.search([
            ('provider', '=', 'twilio'),
            ('external_id', '=', 'Polly.Matthew'),
        ], limit=1) or Voice.create({
            'name': 'Matthew',
            'provider': 'twilio',
            'external_id': 'Polly.Matthew',
        })
        settings = cls.env['connect.settings'].sudo().search([], limit=1)
        if not settings:
            settings = cls.env['connect.settings'].sudo().create({})
        cls.settings = settings

    def _make_audio(self, **vals):
        defaults = {
            'name': 'Test Audio',
            'source': 'twilio_tts',
            'static_text': 'Hello.',
        }
        defaults.update(vals)
        return self.env['connect.audio'].create(defaults)

    def test_use_default_voice_defaults_true(self):
        """New audio rows opt into the DB-wide default by default — operators
        pin a voice only when they deliberately uncheck the knob."""
        audio = self._make_audio()
        self.assertTrue(audio.use_default_voice)

    def test_resolve_voice_uses_default_when_flag_true(self):
        """use_default_voice=True wins even when voice_id is pinned —
        voice_id is intentionally ignored until the operator flips the knob."""
        self.settings.default_twilio_voice = self.joanna
        audio = self._make_audio(
            use_default_voice=True,
            voice_id=self.matthew.id,
        )
        self.assertEqual(audio._resolve_voice(), self.joanna)

    def test_resolve_voice_uses_pinned_when_flag_false(self):
        """use_default_voice=False pins voice_id; settings default ignored."""
        self.settings.default_twilio_voice = self.joanna
        audio = self._make_audio(
            use_default_voice=False,
            voice_id=self.matthew.id,
        )
        self.assertEqual(audio._resolve_voice(), self.matthew)

    def test_resolve_voice_flag_false_no_pin_falls_back_to_default(self):
        """Unchecked knob with no voice_id still reaches the DB default —
        a partially configured audio shouldn't produce silence."""
        self.settings.default_twilio_voice = self.joanna
        audio = self._make_audio(use_default_voice=False, voice_id=False)
        self.assertEqual(audio._resolve_voice(), self.joanna)

    def test_resolve_voice_empty_when_nothing_configured(self):
        """No pin, no default, use_default_voice=True → empty recordset so
        callers fall back to DEFAULT_TWILIO_VOICE in tts_mixin."""
        self.settings.default_twilio_voice = False
        audio = self._make_audio(use_default_voice=True, voice_id=False)
        self.assertFalse(audio._resolve_voice())

    def test_resolved_voice_id_compute_tracks_flag_flip(self):
        """The view-facing readonly field recomputes on use_default_voice
        changes without requiring a write to voice_id."""
        self.settings.default_twilio_voice = self.joanna
        audio = self._make_audio(
            use_default_voice=True,
            voice_id=self.matthew.id,
        )
        self.assertEqual(audio.resolved_voice_id, self.joanna)
        audio.use_default_voice = False
        self.assertEqual(audio.resolved_voice_id, self.matthew)

    def test_default_voice_for_source_only_for_tts_sources(self):
        """Non-TTS sources return empty — voice selection is a TTS-only
        concept and leaking a default onto record/external_url rows would
        break cache keying."""
        self.settings.default_twilio_voice = self.joanna
        Audio = self.env['connect.audio']
        empty = Audio
        self.assertEqual(empty._default_voice_for_source('record'),
                         self.env['connect.voice'])
        self.assertEqual(empty._default_voice_for_source('external_url'),
                         self.env['connect.voice'])
        self.assertEqual(empty._default_voice_for_source('attachment'),
                         self.env['connect.voice'])

    def test_default_voice_for_source_reads_settings(self):
        """twilio_tts reads settings.default_twilio_voice directly —
        changing the setting flips the resolved voice on all audios using
        use_default_voice=True without touching any audio rows."""
        Audio = self.env['connect.audio']
        self.settings.default_twilio_voice = self.joanna
        self.assertEqual(Audio._default_voice_for_source('twilio_tts'),
                         self.joanna)
        self.settings.default_twilio_voice = self.matthew
        self.assertEqual(Audio._default_voice_for_source('twilio_tts'),
                         self.matthew)
