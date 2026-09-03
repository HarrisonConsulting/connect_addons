# -*- coding: utf-8 -*-
"""Reachability BFS over the call-routing graph.

Entry points: inbound DIDs (connect.number), plus module-extension seeds.
Terminal leaves: connect.audio.
"""

from odoo.tests import tagged

from odoo.addons.connect.tests.common import ConnectTestCase


@tagged('post_install', '-at_install')
class TestAudioReachability(ConnectTestCase):
    """BFS from entry points marks is_reachable on the right audios."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Audio = cls.env['connect.audio']
        cls.live_audio = cls.Audio.create({
            'name': 'Reachable Audio',
            'source': 'twilio_tts',
            'static_text': 'reachable',
            # Only reviewed/live audio may be attached to a referrer
            # (connect.audio.referrer.mixin._check_audio_selectable).
            'state': 'reviewed',
        })
        cls.orphan_audio = cls.Audio.create({
            'name': 'Orphan Audio',
            'source': 'twilio_tts',
            'static_text': 'orphan',
        })
        cls.system_audio = cls.Audio.create({
            'name': 'System Audio',
            'source': 'twilio_tts',
            'static_text': 'sys',
            'system_key': 'reach_test.system',
        })

    def _make_callflow(self, prompt_audio):
        # prompt_audio_id lives on the elevenlabs extension of connect.callflow;
        # connect_elevenlabs is installed transitively on this branch.
        return self.env['connect.callflow'].create({
            'name': 'Reach CF',
            'prompt_audio_id': prompt_audio.id if prompt_audio else False,
        })

    def _make_number(self, callflow):
        # Unique number suffix avoids UNIQUE-constraint clashes across runs.
        return self.env['connect.number'].create({
            'phone_number': f'+1500555{self.env.cr.now().microsecond % 10000:04d}',
            'destination': 'callflow',
            'callflow': callflow.id,
        })

    def test_bfs_from_number_to_callflow_to_audio_marks_reachable(self):
        cf = self._make_callflow(self.live_audio)
        self._make_number(cf)
        self.Audio._refresh_reachability()
        self.live_audio.invalidate_recordset(['is_reachable'])
        self.assertTrue(self.live_audio.is_reachable)

    def test_orphan_audio_is_not_reachable(self):
        self.Audio._refresh_reachability()
        self.orphan_audio.invalidate_recordset(['is_reachable'])
        self.assertFalse(self.orphan_audio.is_reachable)

    def test_system_key_audio_always_reachable(self):
        self.Audio._refresh_reachability()
        self.system_audio.invalidate_recordset(['is_reachable'])
        self.assertTrue(self.system_audio.is_reachable)

    def test_refresh_stamps_settings_timestamp(self):
        self.Audio._refresh_reachability()
        settings = self.env['connect.settings'].sudo().search([], limit=1)
        self.assertTrue(settings.last_reachability_refresh_on)
