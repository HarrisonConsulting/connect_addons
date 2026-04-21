# -*- coding: utf-8 -*-
"""μ-law (G.711) transcoder for browser-uploaded recordings.

Why:
  Twilio <Play> plays the WAV we serve. If we hand it 44.1 kHz stereo PCM16,
  the PSTN edge resamples it on every call — lossy, adds first-byte latency.
  Transcoding once at upload time to 8 kHz mono μ-law matches the PSTN format
  end-to-end and halves the file size.

No external dependencies: uses struct + array, with audioop.lin2ulaw as a
fast path on Python versions that still ship it (removed in Python 3.13).
When audioop isn't available we fall back to a precomputed lookup table —
built once at import, 65 kB, zero per-sample allocation.

The transcoder is idempotent: passing an already-8 kHz-mono-μ-law WAV back
in returns the input unchanged.
"""

import logging
import struct
from array import array

logger = logging.getLogger(__name__)


# WAV format codes
_WAVE_PCM = 1
_WAVE_MULAW = 7

TARGET_SAMPLE_RATE = 8000
# Canonical Twilio-friendly mimetype for RIFF-wrapped μ-law output. The old
# 'audio/x-wav' was accepted by Twilio but not the documented canonical; we
# emit 'audio/wav' now and keep 'audio/x-wav'/'audio/wave' as accepted input
# aliases (see connect.audio.TWILIO_PLAYABLE_MIMETYPES).
TARGET_MIMETYPE = 'audio/wav'


# --------------------------------------------------------------------------
# μ-law companding
# --------------------------------------------------------------------------

_BIAS = 0x84
_CLIP = 32635


def _linear_to_ulaw(sample):
    """ITU-T G.711 μ-law companding of a single signed 16-bit sample."""
    if sample < 0:
        sample = -sample
        mask = 0x7F
    else:
        mask = 0xFF
    if sample > _CLIP:
        sample = _CLIP
    sample += _BIAS
    # Find exponent (segment)
    exp = 7
    expMask = 0x4000
    while exp and not (sample & expMask):
        exp -= 1
        expMask >>= 1
    mantissa = (sample >> (exp + 3)) & 0x0F
    return ((exp << 4) | mantissa) ^ mask


def _build_ulaw_table():
    """Precompute μ-law byte for every signed 16-bit input. 65 kB, one-off."""
    table = bytearray(65536)
    for i in range(65536):
        # Interpret i as signed 16-bit via two's-complement
        sample = i - 65536 if i >= 32768 else i
        table[i] = _linear_to_ulaw(sample)
    return bytes(table)


_ULAW_TABLE = _build_ulaw_table()


try:
    import audioop as _audioop  # noqa: F401 — removed in Python 3.13
    _HAS_AUDIOOP = True
except ImportError:
    _HAS_AUDIOOP = False

# Preferred resampler: libsamplerate ("Secret Rabbit Code") via `samplerate`
# Python bindings. BSD-2, manylinux wheel, numpy-only dep. Quality-wise this
# is the industry reference for Python-side sample-rate conversion.
try:
    import numpy as _np
    import samplerate as _samplerate
    _HAS_SAMPLERATE = True
except ImportError:
    _HAS_SAMPLERATE = False

# Fallback to scipy.signal.resample_poly if samplerate isn't installed but
# scipy is. Same polyphase-FIR quality bucket as samplerate.
try:
    from scipy.signal import resample_poly as _resample_poly
    import numpy as _np  # noqa: F811
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


def pcm16_to_ulaw(pcm16_bytes):
    """Compand PCM16 little-endian bytes to μ-law bytes.

    Uses audioop.lin2ulaw when available (C-fast); otherwise a lookup-table
    path that's ~5-10× slower but still O(n) with no per-sample Python calls.
    """
    if _HAS_AUDIOOP:
        return _audioop.lin2ulaw(pcm16_bytes, 2)
    # Lookup-table fallback: index by (sample+32768) into the precomputed table.
    samples = array('h')
    samples.frombytes(pcm16_bytes)
    table = _ULAW_TABLE
    out = bytearray(len(samples))
    for idx, s in enumerate(samples):
        out[idx] = table[s + 32768]
    return bytes(out)


# --------------------------------------------------------------------------
# WAV parsing / building
# --------------------------------------------------------------------------

def _parse_wav(wav_bytes):
    """Return (fmt_dict, data_bytes). Raises ValueError on malformed input.

    Walks RIFF chunks — the 'fmt '/'data' pair may be interleaved with
    metadata chunks (LIST/INFO) that browsers sometimes emit.
    """
    if len(wav_bytes) < 44:
        raise ValueError('WAV shorter than 44 bytes; not a valid file.')
    if wav_bytes[:4] != b'RIFF' or wav_bytes[8:12] != b'WAVE':
        raise ValueError('Missing RIFF/WAVE header.')

    pos = 12
    fmt = None
    data = None
    while pos + 8 <= len(wav_bytes):
        chunk_id = wav_bytes[pos:pos + 4]
        chunk_size = struct.unpack('<I', wav_bytes[pos + 4:pos + 8])[0]
        body = wav_bytes[pos + 8:pos + 8 + chunk_size]
        if chunk_id == b'fmt ':
            if len(body) < 16:
                raise ValueError('fmt chunk too short.')
            (audio_format, channels, sample_rate, byte_rate,
             block_align, bits_per_sample) = struct.unpack('<HHIIHH', body[:16])
            fmt = {
                'audio_format': audio_format,
                'channels': channels,
                'sample_rate': sample_rate,
                'byte_rate': byte_rate,
                'block_align': block_align,
                'bits_per_sample': bits_per_sample,
            }
        elif chunk_id == b'data':
            data = body
            break
        # Chunks are word-aligned: pad odd-sized bodies by one byte.
        pos += 8 + chunk_size + (chunk_size & 1)
    if fmt is None or data is None:
        raise ValueError('WAV missing fmt or data chunk.')
    return fmt, data


def _build_wav(pcm_bytes, *, audio_format, channels, sample_rate, bits_per_sample):
    """Return a minimal 44-byte-header WAV for the given payload."""
    data_size = len(pcm_bytes)
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF', 36 + data_size, b'WAVE',
        b'fmt ', 16, audio_format, channels, sample_rate, byte_rate,
        block_align, bits_per_sample,
        b'data', data_size,
    )
    return header + pcm_bytes


# --------------------------------------------------------------------------
# Resample + downmix
# --------------------------------------------------------------------------

def _downmix_to_mono(pcm16_bytes, channels):
    if channels == 1:
        return pcm16_bytes
    samples = array('h')
    samples.frombytes(pcm16_bytes)
    frames = len(samples) // channels
    out = array('h', [0] * frames)
    for i in range(frames):
        total = 0
        base = i * channels
        for c in range(channels):
            total += samples[base + c]
        out[i] = total // channels
    return out.tobytes()


def _resample_mono_pcm16(pcm16_bytes, src_rate, dst_rate):
    """High-quality polyphase resample, PCM16 bytes in, PCM16 bytes out.

    Preferred: libsamplerate SINC_BEST_QUALITY via the `samplerate` bindings —
    the industry reference for speech-quality SRC. Fallback: scipy's
    resample_poly (Kaiser-windowed polyphase FIR). Last resort: stdlib
    audioop.ratecv, which is a simple linear filter with aliasing — the path
    we're explicitly replacing. Kept only so the module keeps working if
    neither wheel is installed; logs a WARNING so ops notices.
    """
    if src_rate == dst_rate:
        return pcm16_bytes
    if not pcm16_bytes:
        return b''

    if _HAS_SAMPLERATE:
        x = _np.frombuffer(pcm16_bytes, dtype='<i2').astype(_np.float32) / 32768.0
        y = _samplerate.resample(x, dst_rate / src_rate, 'sinc_best')
        y = _np.clip(y * 32768.0, -32768.0, 32767.0).astype('<i2')
        return y.tobytes()

    if _HAS_SCIPY:
        from math import gcd
        g = gcd(src_rate, dst_rate)
        up, down = dst_rate // g, src_rate // g
        x = _np.frombuffer(pcm16_bytes, dtype='<i2').astype(_np.float32)
        y = _resample_poly(x, up, down)
        y = _np.clip(y, -32768.0, 32767.0).astype('<i2')
        return y.tobytes()

    logger.warning(
        'audio_ulaw: samplerate and scipy unavailable, falling back to '
        'audioop.ratecv (aliased linear filter). Install samplerate for '
        'production-quality resampling.')
    if _HAS_AUDIOOP:
        converted, _ = _audioop.ratecv(pcm16_bytes, 2, 1, src_rate, dst_rate, None)
        return converted
    raise ValueError(
        'No resampler available: install samplerate (preferred), scipy, or '
        'run on Python <=3.12 with audioop.')


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def transcode_to_ulaw_wav(wav_bytes):
    """Return a 8 kHz mono μ-law WAV; idempotent.

    Accepts PCM16 WAV (any sample rate, mono or stereo). If the input is
    already μ-law 8 kHz mono, returns it unchanged. Raises ValueError on
    input we can't handle so the caller can surface a clear error.
    """
    fmt, data = _parse_wav(wav_bytes)

    if (fmt['audio_format'] == _WAVE_MULAW
            and fmt['sample_rate'] == TARGET_SAMPLE_RATE
            and fmt['channels'] == 1):
        return wav_bytes

    if fmt['audio_format'] != _WAVE_PCM:
        raise ValueError(
            f'Only PCM16 input is supported; got audio_format={fmt["audio_format"]}.')
    if fmt['bits_per_sample'] != 16:
        raise ValueError(
            f'Only 16-bit PCM supported; got {fmt["bits_per_sample"]}-bit.')
    if fmt['channels'] not in (1, 2):
        raise ValueError(
            f'Only mono/stereo input supported; got {fmt["channels"]} channels.')

    mono = _downmix_to_mono(data, fmt['channels'])
    resampled = _resample_mono_pcm16(mono, fmt['sample_rate'], TARGET_SAMPLE_RATE)
    ulaw = pcm16_to_ulaw(resampled)
    return _build_wav(
        ulaw,
        audio_format=_WAVE_MULAW,
        channels=1,
        sample_rate=TARGET_SAMPLE_RATE,
        bits_per_sample=8,
    )
