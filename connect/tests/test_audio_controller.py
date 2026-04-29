# -*- coding: utf-8 -*-
"""HttpCase tests for the signed-URL controller that serves utterance
binaries to Twilio's media servers.
"""

import base64

from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestAudioController(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Audio = cls.env['connect.audio']
        cls.Utterance = cls.env['connect.audio.utterance']
        cls.audio = cls.Audio.create({
            'name': 'Ctl Test',
            'source': 'twilio_tts',
            'static_text': 'served via url',
        })
        cls.dynamic_audio = cls.Audio.create({
            'name': 'Ctl Dyn Test',
            'source': 'twilio_tts',
            'static_text': 'hello {partner_id.name}',
            'is_dynamic': True,
            'model_id': cls.env['ir.model']._get('res.partner').id,
        })
        mp3_bytes = b'\xff\xfb\x90\x00' + b'\x00' * 64
        cls.static_utterance = cls.Utterance.create({
            'audio_id': cls.audio.id,
            'text_hash': cls.Utterance.hash_text('served via url'),
            'params_hash': '',
            'rendered_text': 'served via url',
            'source_used': 'twilio_tts',
            'filename': 'served.mp3',
            'mimetype': 'audio/mpeg',
            'file': base64.b64encode(mp3_bytes).decode('ascii'),
        })
        cls.dynamic_utterance = cls.Utterance.create({
            'audio_id': cls.dynamic_audio.id,
            'text_hash': cls.Utterance.hash_text('hello Alice'),
            'params_hash': '',
            'rendered_text': 'hello Alice',
            'source_used': 'twilio_tts',
            'filename': 'dyn.mp3',
            'mimetype': 'audio/mpeg',
            'file': base64.b64encode(mp3_bytes).decode('ascii'),
        })
        cls.mp3_bytes = mp3_bytes

    def test_valid_token_returns_200_with_body(self):
        path = self.static_utterance.get_public_path()
        resp = self.url_open(path)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get('Content-Type'), 'audio/mpeg')
        self.assertEqual(resp.content, self.mp3_bytes)

    def test_missing_token_returns_403(self):
        resp = self.url_open(
            f'/connect/audio/utterance/{self.static_utterance.id}')
        self.assertEqual(resp.status_code, 403)

    def test_bad_token_returns_403(self):
        resp = self.url_open(
            f'/connect/audio/utterance/{self.static_utterance.id}?t=deadbeef')
        self.assertEqual(resp.status_code, 403)

    def test_unknown_utterance_returns_404(self):
        resp = self.url_open('/connect/audio/utterance/99999999?t=whatever')
        self.assertEqual(resp.status_code, 404)

    def test_utterance_without_file_returns_404(self):
        text_only = self.Utterance.create({
            'audio_id': self.audio.id,
            'text_hash': self.Utterance.hash_text('text only'),
            'params_hash': '',
            'rendered_text': 'text only',
            'source_used': 'twilio_tts',
        })
        resp = self.url_open(text_only.get_public_path())
        self.assertEqual(resp.status_code, 404)

    def test_served_count_bumps_on_serve(self):
        before = self.static_utterance.served_count
        self.url_open(self.static_utterance.get_public_path())
        self.static_utterance.invalidate_recordset(
            ['served_count', 'served_on'])
        self.assertEqual(self.static_utterance.served_count, before + 1)
        self.assertTrue(self.static_utterance.served_on)

    def test_static_audio_gets_public_cache_control(self):
        """Fix 4: static audios are safe to cache aggressively."""
        resp = self.url_open(self.static_utterance.get_public_path())
        self.assertTrue(
            resp.headers.get('Cache-Control', '').startswith(
                'public, max-age='),
            f"Unexpected Cache-Control: {resp.headers.get('Cache-Control')!r}")

    def test_dynamic_audio_gets_private_no_store(self):
        """Fix 4: dynamic audios carry PII and must not hit shared caches."""
        resp = self.url_open(self.dynamic_utterance.get_public_path())
        self.assertEqual(
            resp.headers.get('Cache-Control'), 'private, no-store')
