# -*- coding: utf-8 -*-
"""TwiML audio() helper fallback routing.

Covers the three render paths:
  - unknown UUID → fallback.unresolved, chatter on twiml
  - archived audio → fallback.archived, chatter on audio
  - live audio → original, no chatter

Plus: rate-limiting per (audio, twiml) pair, graceful degradation when
the fallback audio itself is missing, and the search-view filter that
surfaces twimls referencing archived audios.
"""

import uuid as uuid_lib
from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from odoo.addons.connect.tests.common import ConnectTestCase


@tagged('post_install', '-at_install')
class TestTwimlAudioFallback(ConnectTestCase):
    """audio() helper routes through fallback audios + logs."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Audio = cls.env['connect.audio']
        cls.Twiml = cls.env['connect.twiml']
        # Seeded fallbacks — exist via data/audio.xml. If missing the
        # install is broken and we want the tests to fail loudly.
        cls.fallback_archived = cls.env.ref('connect_hc_core.audio_fallback_archived')
        cls.fallback_unresolved = cls.env.ref(
            'connect_hc_core.audio_fallback_unresolved')

    def _make_twiml(self, body):
        """Create + return a live connect.twiml with body as Jinja TwiML.

        install_mode bypasses the Twilio API round-trip inside create().
        """
        return self.Twiml.with_context(install_mode=True).create({
            'name': f'T-{uuid_lib.uuid4().hex[:8]}',
            'code_type': 'twiml',
            'twiml': (
                '<?xml version="1.0" encoding="UTF-8"?>'
                f'<Response>{body}</Response>'
            ),
        })

    def _make_audio(self, **kw):
        vals = {
            'name': 'Test Audio',
            'source': 'twilio_tts',
            'static_text': 'Hello world',
            'state': 'live',
        }
        vals.update(kw)
        return self.Audio.create(vals)

    # ------------------------------------------------------------------
    # Unresolved path
    # ------------------------------------------------------------------

    def test_unresolved_uuid_renders_fallback_unresolved(self):
        fake = str(uuid_lib.uuid4())
        tw = self._make_twiml(f"{{{{ audio('{fake}') }}}}")
        out = tw.render(request={}, params={})
        self.assertIn('<Say', out, f'Expected fallback <Say> in {out!r}')
        # The fallback.unresolved static_text is "A configuration error
        # occurred. Please contact support."
        self.assertIn('configuration error', out)

    def test_unresolved_logs_on_twiml_chatter(self):
        fake = str(uuid_lib.uuid4())
        tw = self._make_twiml(f"{{{{ audio('{fake}') }}}}")
        before = len(tw.message_ids)
        tw.render(request={}, params={})
        tw.invalidate_recordset(['message_ids', 'last_unresolved_logged_on'])
        self.assertGreater(
            len(tw.message_ids), before,
            'Expected an unresolved-audio chatter post on the twiml')
        bodies = ' '.join(m.body or '' for m in tw.message_ids)
        self.assertIn(fake, bodies,
                      'Chatter body should cite the unresolved uuid')
        self.assertTrue(tw.last_unresolved_logged_on,
                        'last_unresolved_logged_on should be set')

    # ------------------------------------------------------------------
    # Archived path
    # ------------------------------------------------------------------

    def test_archived_audio_renders_fallback_archived(self):
        audio = self._make_audio(static_text='Original wording')
        # Archive via state (matches the action_mark_archived path).
        audio.write({'state': 'archived', 'active': False})
        tw = self._make_twiml(f"{{{{ audio('{audio.uuid}') }}}}")
        out = tw.render(request={}, params={})
        self.assertIn('<Say', out)
        self.assertNotIn('Original wording', out,
                         'Archived audio must NOT render its own text')
        # fallback.archived.static_text: "This message is temporarily
        # unavailable. Please hold."
        self.assertIn('temporarily unavailable', out)

    def test_archived_logs_on_audio_chatter(self):
        audio = self._make_audio()
        audio.write({'state': 'archived', 'active': False})
        tw = self._make_twiml(f"{{{{ audio('{audio.uuid}') }}}}")
        before = len(audio.message_ids)
        tw.render(request={}, params={})
        audio.invalidate_recordset(
            ['message_ids', 'last_fallback_logged_on'])
        self.assertGreater(
            len(audio.message_ids), before,
            'Expected an archived-audio chatter post on the audio record')
        bodies = ' '.join(m.body or '' for m in audio.message_ids)
        self.assertIn(str(tw.id), bodies,
                      'Chatter body should link back to the twiml record')
        self.assertTrue(audio.last_fallback_logged_on,
                        'last_fallback_logged_on should be set')

    def test_active_false_treated_as_archived(self):
        """Odoo's built-in archive (active=False) uses the same fallback
        path — no leakage of draft/reviewed rendering for an archived-via-
        active row. Using write() with just state=archived sets active
        alongside; we mirror that here to ensure the path triggers even
        when state hasn't been explicitly flipped."""
        audio = self._make_audio()
        # Set active=False directly; write() path pushes state → archived
        # for system audios... but this is not a system audio, so we also
        # have to clear active_reference_count constraint. A fresh audio
        # has no refs, so active=False goes through.
        audio.write({'active': False})
        self.assertEqual(audio.state, 'archived',
                         'Archiving via active=False should set state')
        tw = self._make_twiml(f"{{{{ audio('{audio.uuid}') }}}}")
        out = tw.render(request={}, params={})
        self.assertIn('temporarily unavailable', out,
                      'Archived-via-active path should route to '
                      'fallback.archived')

    # ------------------------------------------------------------------
    # Live path — baseline, no fallback, no chatter
    # ------------------------------------------------------------------

    def test_live_audio_no_fallback(self):
        audio = self._make_audio(
            name='Live Audio', static_text='Hello from live')
        tw = self._make_twiml(f"{{{{ audio('{audio.uuid}') }}}}")
        audio_msgs_before = len(audio.message_ids)
        tw_msgs_before = len(tw.message_ids)
        out = tw.render(request={}, params={})
        self.assertIn('Hello from live', out)
        self.assertNotIn('temporarily unavailable', out)
        self.assertNotIn('configuration error', out)
        audio.invalidate_recordset(['message_ids'])
        tw.invalidate_recordset(['message_ids'])
        self.assertEqual(
            len(audio.message_ids), audio_msgs_before,
            'Live-audio path must not post fallback chatter on the audio')
        self.assertEqual(
            len(tw.message_ids), tw_msgs_before,
            'Live-audio path must not post chatter on the twiml either')

    # ------------------------------------------------------------------
    # Rate-limiting
    # ------------------------------------------------------------------

    def test_rate_limit_suppresses_repeat_chatter(self):
        """Two quick-fire renders: first posts, second is suppressed."""
        fake = str(uuid_lib.uuid4())
        tw = self._make_twiml(f"{{{{ audio('{fake}') }}}}")
        tw.render(request={}, params={})
        tw.invalidate_recordset(['message_ids', 'last_unresolved_logged_on'])
        count_after_first = len(tw.message_ids)
        first_stamp = tw.last_unresolved_logged_on
        self.assertTrue(first_stamp)
        tw.render(request={}, params={})
        tw.invalidate_recordset(['message_ids', 'last_unresolved_logged_on'])
        self.assertEqual(
            len(tw.message_ids), count_after_first,
            'Second render within window must not create a new chatter post')
        # Now simulate the window elapsing and re-render — a new post
        # should appear.
        past = first_stamp - timedelta(hours=1)
        tw.sudo().write({'last_unresolved_logged_on': past})
        tw.render(request={}, params={})
        tw.invalidate_recordset(['message_ids'])
        self.assertGreater(
            len(tw.message_ids), count_after_first,
            'A render past the rate-limit window should post again')

    def test_rate_limit_per_audio_pair_archived(self):
        """Archived-path rate-limit uses last_fallback_logged_on on the
        audio — two renders in a row yield one chatter post."""
        audio = self._make_audio()
        audio.write({'state': 'archived', 'active': False})
        tw = self._make_twiml(f"{{{{ audio('{audio.uuid}') }}}}")
        tw.render(request={}, params={})
        audio.invalidate_recordset(
            ['message_ids', 'last_fallback_logged_on'])
        count_after_first = len(audio.message_ids)
        tw.render(request={}, params={})
        audio.invalidate_recordset(['message_ids'])
        self.assertEqual(
            len(audio.message_ids), count_after_first,
            'Second render within window must not re-post on the audio')

    # ------------------------------------------------------------------
    # Missing fallback — graceful degradation
    # ------------------------------------------------------------------

    def test_missing_fallback_audio_graceful(self):
        """If fallback.archived itself is missing, render yields a comment
        marker + ERROR log rather than crashing."""
        audio = self._make_audio()
        audio.write({'state': 'archived', 'active': False})
        tw = self._make_twiml(f"{{{{ audio('{audio.uuid}') }}}}")
        # Swap the fallback out of reach via its system_key. We can't
        # unlink a system_key-bearing audio (the unlink guard refuses),
        # so rotate its key to something the helper won't find. Cleanup
        # restores on teardown.
        original_key = self.fallback_archived.system_key
        self.fallback_archived.sudo().write({'system_key': 'temp.disabled'})
        self.addCleanup(
            lambda: self.fallback_archived.sudo().write(
                {'system_key': original_key}))
        with self.assertLogs(
                'odoo.addons.connect.models.twiml',
                level='ERROR') as cm:
            out = tw.render(request={}, params={})
        self.assertIn('fallback audio missing', out,
                      f'Expected comment marker in {out!r}')
        self.assertTrue(
            any('fallback audio' in m and 'missing' in m for m in cm.output),
            f'Expected ERROR log about missing fallback; got {cm.output}')

    # ------------------------------------------------------------------
    # Search-view filter
    # ------------------------------------------------------------------

    def test_archived_twimls_filter(self):
        """The references_archived_audio domain finds twimls citing
        archived audios and excludes twimls referencing only live ones."""
        live_audio = self._make_audio(name='Still Live')
        archived_audio = self._make_audio(name='Retired')
        archived_audio.write({'state': 'archived', 'active': False})

        live_tw = self._make_twiml(f"{{{{ audio('{live_audio.uuid}') }}}}")
        archived_tw = self._make_twiml(
            f"{{{{ audio('{archived_audio.uuid}') }}}}")

        matches = self.Twiml.search(
            [('referenced_audio_ids.state', '=', 'archived')])
        self.assertIn(archived_tw, matches,
                      'Filter should find twimls citing archived audios')
        self.assertNotIn(live_tw, matches,
                         'Filter must not surface twimls citing only live '
                         'audios')
