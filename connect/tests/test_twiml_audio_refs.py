# -*- coding: utf-8 -*-
"""TwiML audio() helper + connect.audio.uuid reference graph.

Phase 1 tests: uuid field is auto-generated, unique, immutable (with an
opt-in context bypass for migrations); Jinja/TwiPy audio('<uuid>') helpers
resolve via connect.audio.render/play_on and emit inner TwiML verbs; the
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

    def test_jinja_helper_unknown_uuid_silent(self):
        """Unknown UUID renders empty + logs a warning (Phase 1 behavior)."""
        fake = str(uuid_lib.uuid4())
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<Response>{{{{ audio(\'{fake}\') }}}}</Response>'
        )
        with self.assertLogs('odoo.addons.connect.models.twiml',
                             level='WARNING') as cm:
            out = self._render_twiml(body)
        # Empty substitution — <Response></Response> with no verbs inside.
        self.assertNotIn('<Say', out)
        self.assertNotIn('<Play', out)
        self.assertTrue(
            any('unknown audio uuid' in m for m in cm.output),
            f'Expected unknown-uuid warning; got: {cm.output}')

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
