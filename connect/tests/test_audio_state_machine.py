# -*- coding: utf-8 -*-
"""Tests for connect.audio state machine and reference reconciliation."""

from odoo.tests import tagged
from odoo.exceptions import ValidationError

from .common import ConnectTestCase


@tagged('post_install', '-at_install')
class TestAudioStateMachine(ConnectTestCase):
    """State transitions: draft -> reviewed -> live -> archived and back."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Audio = cls.env['connect.audio']
        cls.audio = cls.Audio.create({
            'name': 'State Test Audio',
            'source': 'twilio_tts',
            'static_text': 'hello',
        })

    def test_new_audio_starts_in_draft(self):
        self.assertEqual(self.audio.state, 'draft')
        self.assertFalse(self.audio.has_active_reference)

    def test_mark_reviewed_without_refs_goes_to_reviewed(self):
        self.audio.action_mark_reviewed()
        self.assertEqual(self.audio.state, 'reviewed')

    def test_mark_reviewed_with_active_ref_goes_straight_to_live(self):
        # system_key makes has_active_reference True without a referrer row.
        self.audio.system_key = 'state_test_sys_key'
        self.audio.invalidate_recordset(['has_active_reference'])
        self.audio.action_mark_reviewed()
        self.assertEqual(self.audio.state, 'live')

    def test_mark_reviewed_on_archived_raises(self):
        self.audio.state = 'archived'
        with self.assertRaises(ValidationError):
            self.audio.action_mark_reviewed()

    def test_reconcile_flips_reviewed_to_live_when_active_ref_appears(self):
        self.audio.state = 'reviewed'
        self.audio.system_key = 'reconcile_sys_key'
        self.audio.invalidate_recordset(['has_active_reference'])
        self.audio._reconcile_state_with_references()
        self.assertEqual(self.audio.state, 'live')

    def test_reconcile_flips_live_back_to_reviewed_when_refs_inactive(self):
        self.audio.state = 'live'
        self.audio.system_key = False
        self.audio.invalidate_recordset(['has_active_reference'])
        self.audio._reconcile_state_with_references()
        self.assertEqual(self.audio.state, 'reviewed')

    def test_reconcile_never_touches_draft(self):
        self.audio.state = 'draft'
        self.audio.system_key = 'draft_reconcile_key'
        self.audio.invalidate_recordset(['has_active_reference'])
        self.audio._reconcile_state_with_references()
        self.assertEqual(self.audio.state, 'draft')

    def test_reconcile_never_touches_archived(self):
        self.audio.state = 'archived'
        self.audio.system_key = 'arch_reconcile_key'
        self.audio.invalidate_recordset(['has_active_reference'])
        self.audio._reconcile_state_with_references()
        self.assertEqual(self.audio.state, 'archived')

    def test_archive_stamps_archived_on(self):
        self.audio.state = 'reviewed'
        self.audio.state = 'archived'
        self.assertTrue(self.audio.archived_on)

    def test_leaving_archived_clears_archived_on(self):
        self.audio.state = 'archived'
        self.assertTrue(self.audio.archived_on)
        self.audio.action_unarchive()
        self.assertFalse(self.audio.archived_on)
        self.assertEqual(self.audio.state, 'draft')
