# -*- coding: utf-8 -*-
"""Tests for connect.voice.get_voices(): upstream sync + stale cleanup.

Fix 2 (archive, not unlink): stale voices flip active=False so existing
connect.audio.utterance rows (ondelete='restrict' on voice_id) survive a
provider-side voice deletion.
"""

from unittest.mock import patch, MagicMock

from odoo.tests import tagged

from .common import ElevenLabsTestCase


@tagged('post_install', '-at_install')
class TestElevenLabsVoiceSync(ElevenLabsTestCase):

    def _upstream(self, voice_ids):
        """Build an iterable-returning mock shaped like the SDK response."""
        voices = []
        for vid in voice_ids:
            v = MagicMock()
            v.voice_id = vid
            v.name = f'Voice {vid}'
            v.labels = {}
            v.preview_url = None
            v.fine_tuning = None
            voices.append(v)
        client = MagicMock()
        client.voices.get_all.return_value = MagicMock(voices=voices)
        return client

    def test_new_voices_are_created(self):
        client = self._upstream(['el_new_1', 'el_new_2'])
        with patch.object(
            type(self.env['connect.settings']),
            'get_elevenlabs_client',
            return_value=client,
        ):
            self.env['connect.voice'].get_voices()
        created = self.env['connect.voice'].search([
            ('provider', '=', 'elevenlabs'),
            ('external_id', 'in', ['el_new_1', 'el_new_2']),
        ])
        self.assertEqual(len(created), 2)

    def test_existing_voices_not_duplicated(self):
        self.env['connect.voice'].create({
            'name': 'Pre-existing',
            'provider': 'elevenlabs',
            'external_id': 'el_dupe_check',
        })
        client = self._upstream(['el_dupe_check'])
        with patch.object(
            type(self.env['connect.settings']),
            'get_elevenlabs_client',
            return_value=client,
        ):
            self.env['connect.voice'].get_voices()
        matches = self.env['connect.voice'].search([
            ('provider', '=', 'elevenlabs'),
            ('external_id', '=', 'el_dupe_check'),
        ])
        self.assertEqual(len(matches), 1)

    def test_stale_voices_are_archived(self):
        """Fix 2: voices missing upstream get active=False, not unlinked.

        The row survives so existing utterances (voice_id=restrict) remain
        queryable and the audit trail of "which voice rendered this" holds.
        """
        stale = self.env['connect.voice'].create({
            'name': 'Going Away',
            'provider': 'elevenlabs',
            'external_id': 'el_stale_999',
        })
        stale_id = stale.id
        client = self._upstream(['el_still_here'])
        with patch.object(
            type(self.env['connect.settings']),
            'get_elevenlabs_client',
            return_value=client,
        ):
            self.env['connect.voice'].get_voices()
        stale_post = self.env['connect.voice'].with_context(
            active_test=False).browse(stale_id)
        self.assertTrue(stale_post.exists(),
            'Stale voice must still exist as a DB row after sync.')
        self.assertFalse(stale_post.active,
            'Stale voice must be archived (active=False) after sync.')
