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
        self.audio.write({'active': True})
        self.assertFalse(self.audio.archived_on)
        self.assertEqual(self.audio.state, 'draft')


@tagged('post_install', '-at_install')
class TestAudioReferrerSelectionInvariant(ConnectTestCase):
    """Server-side guard: draft/archived audios cannot be wired as referrers.

    Enforced by connect.audio.referrer.mixin._check_audio_selectable inside
    create/write. Mirrors the view-level domain filter on every picker.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Audio = cls.env['connect.audio']
        cls.reviewed_audio = Audio.create({
            'name': 'Reviewed picker target',
            'source': 'twilio_tts',
            'static_text': 'ready for use',
            'state': 'reviewed',
        })
        cls.draft_audio = Audio.create({
            'name': 'Draft picker target',
            'source': 'twilio_tts',
            'static_text': 'workshop',
        })
        cls.archived_audio = Audio.create({
            'name': 'Archived picker target',
            'source': 'twilio_tts',
            'static_text': 'retired',
        })
        # Archived must be reached via the state column; writing state='archived'
        # triggers the archive-on stamp logic but keeps the row usable for this
        # referrer-side test.
        cls.archived_audio.state = 'archived'

    def test_create_rejects_draft_audio_in_referrer_m2o(self):
        with self.assertRaisesRegex(ValidationError, r'draft or archived'):
            self.env['connect.user'].create({
                'username': 'invariant_draft_create',
                'greeting_audio_id': self.draft_audio.id,
            })

    def test_create_rejects_archived_audio_in_referrer_m2o(self):
        with self.assertRaisesRegex(ValidationError, r'draft or archived'):
            self.env['connect.user'].create({
                'username': 'invariant_archived_create',
                'greeting_audio_id': self.archived_audio.id,
            })

    def test_create_accepts_reviewed_audio(self):
        user = self.env['connect.user'].create({
            'username': 'invariant_reviewed_create',
            'greeting_audio_id': self.reviewed_audio.id,
        })
        self.assertEqual(user.greeting_audio_id, self.reviewed_audio)

    def test_write_rejects_swapping_to_draft(self):
        user = self.env['connect.user'].create({
            'username': 'invariant_swap_draft',
            'greeting_audio_id': self.reviewed_audio.id,
        })
        with self.assertRaisesRegex(ValidationError, r'draft or archived'):
            user.write({'greeting_audio_id': self.draft_audio.id})
        # Post-rollback: original audio still wired.
        self.assertEqual(user.greeting_audio_id, self.reviewed_audio)


@tagged('post_install', '-at_install')
class TestAudioResetToDraft(ConnectTestCase):
    """action_reset_to_draft invariants."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Audio = cls.env['connect.audio']

    def _make(self, state='reviewed', **kw):
        vals = {
            'name': f'reset-{state}',
            'source': 'twilio_tts',
            'static_text': 'x',
            'state': state,
        }
        vals.update(kw)
        return self.Audio.create(vals)

    def test_reset_succeeds_when_untethered(self):
        audio = self._make('reviewed')
        audio.action_reset_to_draft()
        self.assertEqual(audio.state, 'draft')

    def test_reset_noop_when_already_draft(self):
        audio = self._make('draft')
        audio.action_reset_to_draft()
        self.assertEqual(audio.state, 'draft')

    def test_reset_refuses_archived(self):
        audio = self._make('reviewed')
        audio.state = 'archived'
        with self.assertRaisesRegex(ValidationError, r'archived'):
            audio.action_reset_to_draft()

    def test_reset_refuses_system_key(self):
        audio = self._make('reviewed', system_key='test.reset.system')
        with self.assertRaisesRegex(ValidationError, r'system audio'):
            audio.action_reset_to_draft()

    def test_reset_refuses_with_referrer(self):
        audio = self._make('reviewed')
        self.env['connect.user'].create({
            'username': 'reset_with_ref',
            'greeting_audio_id': audio.id,
        })
        audio.invalidate_recordset(['reference_count'])
        with self.assertRaisesRegex(ValidationError, r'still referenced'):
            audio.action_reset_to_draft()
