# -*- coding: utf-8 -*-
"""Tests for connect.audio.utterance: hashing, UNIQUE constraint, cache
lookup behavior that connect.audio.render() depends on.
"""

from psycopg2 import IntegrityError

from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.connect.tests.common import ConnectTestCase


@tagged('post_install', '-at_install')
class TestAudioUtteranceCache(ConnectTestCase):
    """Cache key = (audio_id, voice_id, text_hash, params_hash)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Audio = cls.env['connect.audio']
        cls.Utterance = cls.env['connect.audio.utterance']
        cls.audio = cls.Audio.create({
            'name': 'Cache Test',
            'source': 'twilio_tts',
            'static_text': 'cache me',
        })

    def test_hash_text_stable_across_calls(self):
        self.assertEqual(
            self.Utterance.hash_text('abc'),
            self.Utterance.hash_text('abc'),
        )
        self.assertNotEqual(
            self.Utterance.hash_text('abc'),
            self.Utterance.hash_text('abcd'),
        )

    def test_hash_text_empty_and_none_collapse(self):
        self.assertEqual(
            self.Utterance.hash_text(''),
            self.Utterance.hash_text(None),
        )

    def test_hash_params_empty_is_empty_string(self):
        self.assertEqual(self.Utterance.hash_params(None), '')
        self.assertEqual(self.Utterance.hash_params({}), '')

    def test_hash_params_key_order_independent(self):
        self.assertEqual(
            self.Utterance.hash_params({'a': 1, 'b': 2}),
            self.Utterance.hash_params({'b': 2, 'a': 1}),
        )
        self.assertNotEqual(
            self.Utterance.hash_params({'a': 1}),
            self.Utterance.hash_params({'a': 2}),
        )

    def test_unique_constraint_on_audio_voice_text_params(self):
        vals = {
            'audio_id': self.audio.id,
            'voice_id': False,
            'text_hash': self.Utterance.hash_text('hi'),
            'params_hash': '',
            'rendered_text': 'hi',
            'source_used': 'twilio_tts',
        }
        self.Utterance.create(vals)
        with self.assertRaises(IntegrityError), \
                mute_logger('odoo.sql_db'), self.cr.savepoint():
            self.Utterance.create(vals)

    def test_render_returns_cached_utterance_on_second_call(self):
        u1 = self.audio.render()
        u2 = self.audio.render()
        self.assertEqual(u1.id, u2.id)

    def test_render_creates_utterance_with_correct_hashes(self):
        u = self.audio.render()
        self.assertEqual(u.audio_id, self.audio)
        self.assertEqual(u.text_hash, self.Utterance.hash_text('cache me'))
        self.assertEqual(u.source_used, 'twilio_tts')

    def test_different_text_produces_different_utterance(self):
        u1 = self.audio.render()
        self.audio.static_text = 'something else'
        u2 = self.audio.render()
        self.assertNotEqual(u1.id, u2.id)
