# -*- coding: utf-8 -*-
"""μ-law transcoder tests - pure function, no Odoo ORM required.

These test the stable public contract of transcode_to_ulaw_wav(): the
header shape, idempotency, stereo downmix, and companding parity between
the audioop fast path and the lookup-table fallback. Quality thresholds
(SNR) are not asserted here.
"""

import math
import struct

from odoo.tests import TransactionCase, tagged

from odoo.addons.connect_hc_core.models import audio_ulaw


def _build_pcm16_wav(samples, sample_rate, channels):
    """Minimal 44-byte-header PCM16 WAV for test input generation."""
    pcm = struct.pack('<' + 'h' * len(samples), *samples)
    data_size = len(pcm)
    byte_rate = sample_rate * channels * 2
    block_align = channels * 2
    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF', 36 + data_size, b'WAVE',
        b'fmt ', 16, 1, channels, sample_rate, byte_rate,
        block_align, 16,
        b'data', data_size,
    )
    return header + pcm


@tagged('post_install', '-at_install')
class TestAudioUlawTranscode(TransactionCase):

    def test_output_is_8khz_mono_mulaw(self):
        samples = [int(16000 * math.sin(2 * math.pi * 440 * i / 44100))
                   for i in range(4410)]
        wav_in = _build_pcm16_wav(samples, sample_rate=44100, channels=1)
        wav_out = audio_ulaw.transcode_to_ulaw_wav(wav_in)
        fmt, _data = audio_ulaw._parse_wav(wav_out)
        self.assertEqual(fmt['audio_format'], 7)  # μ-law (G.711)
        self.assertEqual(fmt['sample_rate'], 8000)
        self.assertEqual(fmt['channels'], 1)
        self.assertEqual(fmt['bits_per_sample'], 8)

    def test_idempotent(self):
        samples = [int(8000 * math.sin(2 * math.pi * 300 * i / 8000))
                   for i in range(800)]
        wav_in = _build_pcm16_wav(samples, sample_rate=8000, channels=1)
        once = audio_ulaw.transcode_to_ulaw_wav(wav_in)
        twice = audio_ulaw.transcode_to_ulaw_wav(once)
        self.assertEqual(
            once, twice,
            'Re-transcoding a μ-law 8 kHz mono WAV must be a no-op.')

    def test_stereo_input_is_downmixed(self):
        # Interleaved L/R that cancel to zero mean - exercises the downmix path.
        samples = []
        for i in range(2205):
            samples.append(10000)
            samples.append(-10000)
        wav_in = _build_pcm16_wav(samples, sample_rate=44100, channels=2)
        wav_out = audio_ulaw.transcode_to_ulaw_wav(wav_in)
        fmt, _ = audio_ulaw._parse_wav(wav_out)
        self.assertEqual(fmt['channels'], 1)

    def test_non_pcm_raises_value_error(self):
        # audio_format=6 is A-law, not supported.
        header = struct.pack(
            '<4sI4s4sIHHIIHH4sI',
            b'RIFF', 44, b'WAVE',
            b'fmt ', 16, 6, 1, 8000, 8000, 1, 8,
            b'data', 8,
        ) + b'\x00' * 8
        with self.assertRaises(ValueError):
            audio_ulaw.transcode_to_ulaw_wav(header)

    def test_lookup_table_matches_audioop_when_available(self):
        """pcm16_to_ulaw byte-equivalence across the fast and fallback paths.
        Ensures Python 3.13+ readiness (audioop is removed there).
        """
        pcm = struct.pack(
            '<' + 'h' * 1000,
            *[int(20000 * math.sin(2 * math.pi * 440 * i / 8000))
              for i in range(1000)])
        original = audio_ulaw._HAS_AUDIOOP
        if not original:
            self.skipTest('audioop not available; only the table path exists.')
        try:
            audio_ulaw._HAS_AUDIOOP = False
            via_table = audio_ulaw.pcm16_to_ulaw(pcm)
            audio_ulaw._HAS_AUDIOOP = True
            via_audioop = audio_ulaw.pcm16_to_ulaw(pcm)
            self.assertEqual(
                via_table, via_audioop,
                'Lookup-table path must match audioop path byte-for-byte.')
        finally:
            audio_ulaw._HAS_AUDIOOP = original
