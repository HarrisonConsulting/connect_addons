# -*- coding: utf-8 -*-
import io
import wave

from odoo.tests import TransactionCase, tagged, new_test_user

from odoo.addons.connect.models.recording import silence_wav_spans


def _tone():
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(8000)
        writer.writeframes(b'\x7f\x7f' * 8000)
    return buffer.getvalue()


@tagged('post_install', '-at_install')
class TestRecordingMarks(TransactionCase):

    def test_pause_until_resume_and_stop_until_end(self):
        Mark = self.env['connect.recording.mark']

        class _Point:
            def __init__(self, kind, offset_ms):
                self.kind = kind
                self.offset_ms = offset_ms

        spans = Mark.spans_ms([
            _Point('start', 0),
            _Point('pause', 1000),
            _Point('resume', 2500),
            _Point('stop', 4000),
        ], 8000)
        self.assertEqual(spans, [(1000, 2500), (4000, 8000)])

    def test_wav_span_is_silenced(self):
        original = _tone()
        redacted = silence_wav_spans(original, [(0, 500)])
        with wave.open(io.BytesIO(redacted), 'rb') as reader:
            frames = reader.readframes(reader.getnframes())
        self.assertEqual(frames[:8000], b'\x00' * 8000)
        self.assertNotEqual(frames[8000:8002], b'\x00\x00')

    def test_record_all_calls_overrides_a_user_opt_out(self):
        settings = self.env['connect.settings'].sudo()
        odoo_user = new_test_user(
            self.env, login='rec_all_user', groups='base.group_user')
        user = self.env['connect.user'].with_context(
            no_clear_cache=True, no_twilio_create=True).create({
                'user': odoo_user.id,
                'record_calls': False,
            })
        settings.set_param('record_all_calls', True)
        self.assertTrue(settings.calls_are_recorded(user=user))
        settings.set_param('record_all_calls', False)
        self.assertFalse(settings.calls_are_recorded(user=user))
