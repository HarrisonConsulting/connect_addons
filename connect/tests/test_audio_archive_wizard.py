# -*- coding: utf-8 -*-
"""Tests for connect.audio.archive.wizard: the safety gate between
archiving an audio and silently breaking a live callflow.
"""

from odoo.tests import tagged
from odoo.exceptions import UserError, AccessError

from .common import ConnectTestCase


@tagged('post_install', '-at_install')
class TestAudioArchiveWizard(ConnectTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Audio = cls.env['connect.audio']
        cls.audio = cls.Audio.create({
            'name': 'Archivable',
            'source': 'twilio_tts',
            'static_text': 'bye',
        })
        # Only reviewed/live audios are selectable as a referrer, so a draft
        # fixture cannot be attached to the callflows these tests need.
        cls.audio.action_mark_reviewed()

    def _make_callflow_using(self, audio):
        return self.env['connect.callflow'].create({
            'name': 'Wizard Test CF',
            'prompt_audio_id': audio.id,
        })

    def test_archive_with_no_refs_flips_state_directly(self):
        result = self.audio.action_archive_audio()
        self.assertIs(result, True)
        self.assertEqual(self.audio.state, 'archived')

    def test_archive_with_active_refs_opens_wizard(self):
        self._make_callflow_using(self.audio)
        self.audio._refresh_references()
        action = self.audio.action_archive_audio()
        self.assertEqual(action['res_model'], 'connect.audio.archive.wizard')
        self.assertEqual(action['view_mode'], 'form')

    def test_wizard_leave_blocks_archive(self):
        self._make_callflow_using(self.audio)
        self.audio._refresh_references()
        action = self.audio.action_archive_audio()
        wizard = self.env['connect.audio.archive.wizard'].browse(action['res_id'])
        # All lines default to 'leave' for active refs - apply must raise.
        with self.assertRaises(UserError):
            wizard.action_apply_and_archive()
        self.assertNotEqual(self.audio.state, 'archived')

    def test_wizard_clear_blanks_referrer_and_archives(self):
        cf = self._make_callflow_using(self.audio)
        self.audio._refresh_references()
        action = self.audio.action_archive_audio()
        wizard = self.env['connect.audio.archive.wizard'].browse(action['res_id'])
        wizard.line_ids.write({'action': 'clear'})
        wizard.action_apply_and_archive()
        self.assertFalse(cf.prompt_audio_id)
        self.assertEqual(self.audio.state, 'archived')

    def test_wizard_swap_repoints_and_archives(self):
        replacement = self.Audio.create({
            'name': 'Replacement',
            'source': 'twilio_tts',
            'static_text': 'new',
        })
        # The swap writes this audio onto the callflow, so it has to be
        # selectable too — a draft replacement trips the same referrer guard.
        replacement.action_mark_reviewed()
        cf = self._make_callflow_using(self.audio)
        self.audio._refresh_references()
        action = self.audio.action_archive_audio()
        wizard = self.env['connect.audio.archive.wizard'].browse(action['res_id'])
        wizard.line_ids.write({
            'action': 'swap',
            'replacement_audio_id': replacement.id,
        })
        wizard.action_apply_and_archive()
        self.assertEqual(cf.prompt_audio_id, replacement)
        self.assertEqual(self.audio.state, 'archived')

    def test_wizard_swap_without_replacement_raises(self):
        self._make_callflow_using(self.audio)
        self.audio._refresh_references()
        action = self.audio.action_archive_audio()
        wizard = self.env['connect.audio.archive.wizard'].browse(action['res_id'])
        with self.assertRaises(UserError):
            wizard.line_ids.write({'action': 'swap'})

    def test_non_admin_cannot_create_wizard(self):
        """Fix 1: wizard has admin-only ACLs; non-admin create must fail."""
        self._make_callflow_using(self.audio)
        self.audio._refresh_references()
        user = self.env['res.users'].create({
            'name': 'Connect User',
            'login': f'connect_user_{self.env.cr.now().microsecond}',
            'group_ids': [(6, 0, [
                self.env.ref('connect.group_connect_user').id,
                self.env.ref('base.group_user').id,
            ])],
        })
        with self.assertRaises(AccessError):
            self.env['connect.audio.archive.wizard'].with_user(user).create({
                'audio_id': self.audio.id,
            })
