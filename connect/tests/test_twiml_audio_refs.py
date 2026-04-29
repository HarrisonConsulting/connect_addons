# -*- coding: utf-8 -*-
"""TwiML audio() helper + connect.audio.uuid reference graph.

uuid field is auto-generated, unique, immutable (with an opt-in context
bypass for migrations); Jinja/TwiPy audio('<uuid>') helpers resolve via
connect.audio.render/play_on and emit inner TwiML verbs; the
`referenced_audio_ids` M2m on connect.twiml is populated by scanning the
body so Where-Used and reachability work for TwiML like they do for
callflows.
"""

import re
import uuid as uuid_lib

from psycopg2 import IntegrityError

from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import ConnectTestCase


@tagged('post_install', '-at_install')
class TestAudioUuid(ConnectTestCase):
    """UUID field on connect.audio: auto-generated, unique, immutable."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Audio = cls.env['connect.audio']

    def test_uuid_auto_generated(self):
        audio = self.Audio.create({
            'name': 'Auto UUID',
            'source': 'twilio_tts',
            'static_text': 'hi',
        })
        self.assertTrue(audio.uuid, 'uuid default should have populated')
        # Parse-verify — default is str(uuid4()); any well-formed v4 is fine.
        parsed = uuid_lib.UUID(audio.uuid)
        self.assertEqual(parsed.version, 4)

    def test_uuid_unique(self):
        shared = str(uuid_lib.uuid4())
        self.Audio.create({
            'name': 'First',
            'source': 'twilio_tts',
            'static_text': 'one',
            'uuid': shared,
        })
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'):
            with self.env.cr.savepoint():
                self.Audio.create({
                    'name': 'Second',
                    'source': 'twilio_tts',
                    'static_text': 'two',
                    'uuid': shared,
                })

    def test_uuid_immutable(self):
        audio = self.Audio.create({
            'name': 'Immutable',
            'source': 'twilio_tts',
            'static_text': 'x',
        })
        different = str(uuid_lib.uuid4())
        with self.assertRaises(ValidationError):
            audio.write({'uuid': different})

    def test_uuid_immutable_same_value_is_noop(self):
        """Writing the current value back is allowed — prevents spurious
        errors when a form save includes the unchanged uuid in vals."""
        audio = self.Audio.create({
            'name': 'Same',
            'source': 'twilio_tts',
            'static_text': 'x',
        })
        current = audio.uuid
        audio.write({'uuid': current})
        self.assertEqual(audio.uuid, current)

    def test_uuid_immutable_bypass_with_context(self):
        """Migrations set allow_uuid_write=True to backfill baked UUIDs."""
        audio = self.Audio.create({
            'name': 'Bypass',
            'source': 'twilio_tts',
            'static_text': 'x',
        })
        new_uuid = str(uuid_lib.uuid4())
        audio.with_context(allow_uuid_write=True).write({'uuid': new_uuid})
        audio.invalidate_recordset(['uuid'])
        self.assertEqual(audio.uuid, new_uuid)

    def test_uuid_regenerated_on_copy(self):
        """copy=False on the field → copy() mints a fresh uuid instead of
        duplicating the original, preserving the unique constraint."""
        original = self.Audio.create({
            'name': 'Original',
            'source': 'twilio_tts',
            'static_text': 'x',
        })
        dup = original.copy()
        self.assertTrue(dup.uuid, 'Copy should have a non-empty uuid')
        self.assertNotEqual(dup.uuid, original.uuid,
                            'Copy must not share uuid with the original')
        # And it's still a valid v4.
        self.assertEqual(uuid_lib.UUID(dup.uuid).version, 4)

    def test_uuid_immutable_null_to_set_blocked(self):
        """NULL→set path is guarded too — only allow_uuid_write may mint
        a uuid, otherwise a caller could pick a value that collides with a
        baked system UUID (or any other existing reference key)."""
        audio = self.Audio.create({
            'name': 'Null to set',
            'source': 'twilio_tts',
            'static_text': 'x',
        })
        # Simulate a pre-backfill row with no uuid.
        self.env.cr.execute(
            'UPDATE connect_audio SET uuid = NULL WHERE id = %s',
            (audio.id,))
        audio.invalidate_recordset(['uuid'])
        with self.assertRaises(ValidationError):
            audio.write({'uuid': str(uuid_lib.uuid4())})


@tagged('post_install', '-at_install')
class TestTwimlAudioHelper(ConnectTestCase):
    """Jinja + TwiPy audio('<uuid>') helpers resolve to inner TwiML verbs."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Audio = cls.env['connect.audio']
        cls.Twiml = cls.env['connect.twiml']
        cls.tts_audio = cls.Audio.create({
            'name': 'Welcome TTS',
            'source': 'twilio_tts',
            'static_text': 'Hello from audio helper',
            'state': 'live',
        })

    def _render_twiml(self, body, code_type='twiml'):
        vals = {
            'name': f'T-{uuid_lib.uuid4().hex[:8]}',
            'code_type': code_type,
        }
        if code_type == 'twiml':
            vals['twiml'] = body
        else:
            # twipy requires a seed twiml value via the required=True column.
            vals['twiml'] = '<Response/>'
            vals['twipy'] = body
        # install_mode skips the Twilio API round-trip in create().
        twiml = self.Twiml.with_context(install_mode=True).create(vals)
        return twiml.render(request={}, params={})

    def test_jinja_helper_resolves_to_say(self):
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<Response>{{{{ audio(\'{self.tts_audio.uuid}\') }}}}</Response>'
        )
        out = self._render_twiml(body)
        self.assertIn('<Say', out,
                      f'Expected <Say> in rendered output; got: {out!r}')
        self.assertIn('Hello from audio helper', out)

    def test_jinja_helper_unknown_uuid_renders_fallback(self):
        """Unknown UUID routes to fallback.unresolved + logs WARNING.

        Full end-to-end coverage (chatter posts, rate-limit) lives in
        test_twiml_audio_fallback.
        """
        fake = str(uuid_lib.uuid4())
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<Response>{{{{ audio(\'{fake}\') }}}}</Response>'
        )
        with self.assertLogs('odoo.addons.connect.models.twiml',
                             level='WARNING') as cm:
            out = self._render_twiml(body)
        # Fallback renders the unresolved-system-audio text.
        self.assertIn('<Say', out, f'Expected fallback <Say>; got {out!r}')
        self.assertIn('configuration error', out)
        self.assertTrue(
            any('reason=unresolved' in m for m in cm.output),
            f'Expected reason=unresolved warning; got: {cm.output}')

    def test_jinja_helper_preserves_attributes(self):
        """ET round-trip must preserve verb attributes — e.g. <Say voice="...">
        from play_on's voice resolution. If the helper's inner-verb
        extraction dropped attributes, rendered TwiML would regress to the
        default voice without the operator noticing."""
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<Response>{{{{ audio(\'{self.tts_audio.uuid}\') }}}}</Response>'
        )
        out = self._render_twiml(body)
        # voice="..." attribute should have survived ET serialization.
        self.assertRegex(
            out, r'<Say[^>]*\svoice="[^"]+"',
            f'Expected <Say> to carry a voice="..." attribute; got: {out!r}')

    def test_jinja_helper_uppercase_uuid_resolves(self):
        """Bodies may paste uppercase hex — scanner and lookup normalize."""
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<Response>{{{{ audio(\'{self.tts_audio.uuid.upper()}\') }}}}</Response>'
        )
        out = self._render_twiml(body)
        self.assertIn('<Say', out,
                      f'Expected <Say> in rendered output; got: {out!r}')
        self.assertIn('Hello from audio helper', out)

    def test_twipy_helper_resolves(self):
        body = (
            "from twilio.twiml.voice_response import VoiceResponse\n"
            "response = VoiceResponse()\n"
            f"inner = audio('{self.tts_audio.uuid}')\n"
            # Hand-compose: the helper returns inner verbs as a string so
            # authors can build the outer Response themselves.
            "self.twiml = '<Response>' + str(inner) + '</Response>'\n"
        )
        out = self._render_twiml(body, code_type='twipy')
        self.assertIn('<Say', out)
        self.assertIn('Hello from audio helper', out)


@tagged('post_install', '-at_install')
class TestTwimlReferencedAudio(ConnectTestCase):
    """referenced_audio_ids scanning + Where-Used + reachability wiring."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Audio = cls.env['connect.audio']
        cls.Twiml = cls.env['connect.twiml']
        cls.Reference = cls.env['connect.audio.reference']
        cls.audio_a = cls.Audio.create({
            'name': 'Audio A',
            'source': 'twilio_tts',
            'static_text': 'A',
            'state': 'live',
        })
        cls.audio_b = cls.Audio.create({
            'name': 'Audio B',
            'source': 'twilio_tts',
            'static_text': 'B',
            'state': 'live',
        })
        cls.audio_c = cls.Audio.create({
            'name': 'Audio C',
            'source': 'twilio_tts',
            'static_text': 'C',
            'state': 'live',
        })

    def _make_twiml(self, body, code_type='twiml', **extra):
        vals = {
            'name': f'T-{uuid_lib.uuid4().hex[:8]}',
            'code_type': code_type,
        }
        if code_type == 'twiml':
            vals['twiml'] = body
        else:
            vals['twiml'] = '<Response/>'
            vals['twipy'] = body
        vals.update(extra)
        return self.Twiml.with_context(install_mode=True).create(vals)

    def test_referenced_audio_ids_scanned_on_create(self):
        body = (
            '<Response>'
            f'{{{{ audio(\'{self.audio_a.uuid}\') }}}}'
            f'{{{{ audio(\'{self.audio_b.uuid}\') }}}}'
            '</Response>'
        )
        tw = self._make_twiml(body)
        self.assertIn(self.audio_a, tw.referenced_audio_ids)
        self.assertIn(self.audio_b, tw.referenced_audio_ids)
        self.assertNotIn(self.audio_c, tw.referenced_audio_ids)

    def test_referenced_audio_ids_updates_on_edit(self):
        body_a = f'<Response>{{{{ audio(\'{self.audio_a.uuid}\') }}}}</Response>'
        tw = self._make_twiml(body_a)
        self.assertEqual(tw.referenced_audio_ids, self.audio_a)

        body_bc = (
            '<Response>'
            f'{{{{ audio(\'{self.audio_b.uuid}\') }}}}'
            f'{{{{ audio(\'{self.audio_c.uuid}\') }}}}'
            '</Response>'
        )
        # Bypass the twilio_auto_sync path; this test doesn't talk to Twilio.
        tw.env['connect.settings'].set_param('twilio_auto_sync', False)
        tw.write({'twiml': body_bc})
        tw.invalidate_recordset(['referenced_audio_ids'])
        self.assertNotIn(self.audio_a, tw.referenced_audio_ids)
        self.assertIn(self.audio_b, tw.referenced_audio_ids)
        self.assertIn(self.audio_c, tw.referenced_audio_ids)

    def test_referenced_audio_ids_scans_twipy(self):
        # str-concat form — audio() returns inner verbs as a Markup string,
        # not a TwiML element. Feeding it directly to response.say() would
        # nest <Say><Say ...>; authors build the outer <Response> themselves.
        body = (
            "from twilio.twiml.voice_response import VoiceResponse\n"
            "response = VoiceResponse()\n"
            f"inner = audio('{self.audio_a.uuid}')\n"
            "self.twiml = '<Response>' + str(inner) + '</Response>'\n"
        )
        tw = self._make_twiml(body, code_type='twipy')
        self.assertEqual(tw.referenced_audio_ids, self.audio_a)

    def test_audio_reference_rows_created_for_twiml(self):
        """End-to-end: mixin write-hook fires the ref refresh without help.

        _refresh_references_async falls back to inline when queue_job isn't
        installed — in a TransactionCase without queue_job wiring the row
        should appear at create() time. Also assert that rewriting the body
        to a different audio flips the reference rows correctly (old gone,
        new present) with no manual refresh."""
        body = f'<Response>{{{{ audio(\'{self.audio_a.uuid}\') }}}}</Response>'
        tw = self._make_twiml(body)
        refs_a = self.Reference.search([
            ('audio_id', '=', self.audio_a.id),
            ('referrer_model', '=', 'connect.twiml'),
            ('referrer_res_id', '=', tw.id),
        ])
        self.assertTrue(
            refs_a,
            'Expected a connect.audio.reference row pointing at twiml '
            'without a manual _refresh_references() call')

        # Rewrite the body to reference audio_b instead — old row must go,
        # new row must appear, all driven by the mixin's write-hook.
        tw.env['connect.settings'].set_param('twilio_auto_sync', False)
        tw.write({
            'twiml': f'<Response>{{{{ audio(\'{self.audio_b.uuid}\') }}}}</Response>',
        })
        refs_a_after = self.Reference.search([
            ('audio_id', '=', self.audio_a.id),
            ('referrer_model', '=', 'connect.twiml'),
            ('referrer_res_id', '=', tw.id),
        ])
        refs_b_after = self.Reference.search([
            ('audio_id', '=', self.audio_b.id),
            ('referrer_model', '=', 'connect.twiml'),
            ('referrer_res_id', '=', tw.id),
        ])
        self.assertFalse(
            refs_a_after,
            'Old audio_a reference row should be removed when body rewrites '
            'away from it')
        self.assertTrue(
            refs_b_after,
            'New audio_b reference row should appear after body rewrite')

    def test_model_method_ungoverned(self):
        """code_type='model_method' never populates referenced_audio_ids."""
        tw = self._make_twiml(
            '<Response/>',
            code_type='model_method',
            model='connect.audio',
            method='render',
        )
        # Even if someone sneaks an audio() call into the stored twiml text,
        # we don't scan it for model_method.
        tw.env['connect.settings'].set_param('twilio_auto_sync', False)
        tw.write({
            'twiml': f'<Response>audio(\'{self.audio_a.uuid}\')</Response>',
        })
        tw.invalidate_recordset(['referenced_audio_ids'])
        self.assertFalse(tw.referenced_audio_ids)

    def test_reachability_via_twiml_audio(self):
        """BFS: number(destination=twiml) -> twiml -> referenced audio."""
        body = f'<Response>{{{{ audio(\'{self.audio_a.uuid}\') }}}}</Response>'
        tw = self._make_twiml(body)
        # Create an exten + inbound number that routes to this twiml.
        self.env['connect.number'].create({
            'phone_number': f'+1500556{uuid_lib.uuid4().int % 10000:04d}',
            'destination': 'twiml',
            'twiml': tw.id,
        })
        self.Audio._refresh_reachability()
        self.audio_a.invalidate_recordset(['is_reachable'])
        self.assertTrue(
            self.audio_a.is_reachable,
            'audio_a should be reachable via number->twiml->audio')
