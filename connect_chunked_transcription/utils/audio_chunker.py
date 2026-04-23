"""Chunk long audio (MP3/WAV/FLAC/OGG) at silence boundaries for Whisper.

Pure Python: miniaudio (decode) + numpy (RMS) + stdlib wave (encode). No ffmpeg.

Decoded to int16 mono 16 kHz — Whisper's native rate; also keeps memory
bounded (a 30 min recording is ~57 MB of PCM vs hundreds of MB at 48 kHz f32).
"""
from __future__ import annotations

import io
import logging
import wave
from typing import Optional

import miniaudio
import numpy as np

_log = logging.getLogger(__name__)

_TARGET_SR = 16_000
_TARGET_CH = 1
_RMS_WINDOW_MS = 20  # frame size for RMS analysis


def _decode_to_pcm16_mono_16k(data: bytes, filename: str) -> np.ndarray:
    """Decode any miniaudio-supported format to int16 mono 16 kHz. Returns 1-D np.int16."""
    try:
        decoded = miniaudio.decode(
            data,
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=_TARGET_CH,
            sample_rate=_TARGET_SR,
        )
    except miniaudio.DecodeError as exc:
        raise ValueError(f"cannot decode audio {filename!r}: {exc}") from exc
    return np.frombuffer(decoded.samples, dtype=np.int16)


def _rms_db(frames: np.ndarray) -> np.ndarray:
    """Per-frame RMS in dBFS. frames: (n_frames, window_samples) int16."""
    # Promote to float32 — int16 squared overflows int32 on wide frames.
    f = frames.astype(np.float32)
    rms = np.sqrt(np.mean(f * f, axis=1) + 1e-12)
    return 20.0 * np.log10(rms / 32767.0 + 1e-12)


def _find_silence_windows(
    pcm: np.ndarray, threshold_db: float, min_silence_ms: int
) -> list[tuple[int, int]]:
    """Return list of (start_sample, end_sample) silent regions."""
    win = int(_TARGET_SR * _RMS_WINDOW_MS / 1000)
    n_windows = len(pcm) // win
    if n_windows == 0:
        return []
    frames = pcm[: n_windows * win].reshape(n_windows, win)
    is_silent = _rms_db(frames) < threshold_db

    windows: list[tuple[int, int]] = []
    i = 0
    min_frames = max(1, min_silence_ms // _RMS_WINDOW_MS)
    while i < n_windows:
        if is_silent[i]:
            j = i
            while j < n_windows and is_silent[j]:
                j += 1
            if (j - i) >= min_frames:
                windows.append((i * win, j * win))
            i = j
        else:
            i += 1
    return windows


def _pick_cut_point(
    silences: list[tuple[int, int]], target_sample: int, window_samples: int
) -> Optional[int]:
    """Among silences overlapping [target - window, target + window], pick the
    longest; cut at its midpoint. Returns None if no suitable silence."""
    lo = target_sample - window_samples
    hi = target_sample + window_samples
    candidates = [(s, e) for s, e in silences if e >= lo and s <= hi]
    if not candidates:
        return None
    s, e = max(candidates, key=lambda se: se[1] - se[0])
    return (s + e) // 2


def _write_wav(pcm: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(_TARGET_CH)
        w.setsampwidth(2)  # int16 = 2 bytes
        w.setframerate(_TARGET_SR)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def chunk_audio_at_silences(
    input_bytes: bytes,
    input_filename: str,
    target_chunk_seconds: float = 180.0,
    min_silence_duration_ms: int = 500,
    silence_rms_db: float = -40.0,
    boundary_search_window_seconds: float = 30.0,
) -> list[dict]:
    """Split audio into chunks near silence boundaries.

    Returns list of dicts with keys:
      bytes                   — WAV-encoded int16 mono 16 kHz chunk
      filename                — "chunk_001.wav"
      start_offset_seconds    — absolute offset from start of original audio
      duration_seconds        — chunk duration
    """
    pcm = _decode_to_pcm16_mono_16k(input_bytes, input_filename)
    total_samples = len(pcm)
    if total_samples == 0:
        return []

    silences = _find_silence_windows(pcm, silence_rms_db, min_silence_duration_ms)
    target_samples = int(target_chunk_seconds * _TARGET_SR)
    search_window = int(boundary_search_window_seconds * _TARGET_SR)

    cuts = [0]
    cursor = 0
    while cursor + target_samples < total_samples:
        provisional = cursor + target_samples
        picked = _pick_cut_point(silences, provisional, search_window)
        if picked is None or picked <= cursor:
            _log.warning(
                "no silence within +/-%.0fs of target %.1fs in %r; hard-cutting",
                boundary_search_window_seconds,
                provisional / _TARGET_SR, input_filename,
            )
            picked = provisional
        cuts.append(picked)
        cursor = picked
    cuts.append(total_samples)

    chunks: list[dict] = []
    for idx in range(len(cuts) - 1):
        a, b = cuts[idx], cuts[idx + 1]
        segment = pcm[a:b]
        chunks.append({
            "bytes": _write_wav(segment),
            "filename": f"chunk_{idx + 1:03d}.wav",
            "start_offset_seconds": a / _TARGET_SR,
            "duration_seconds": (b - a) / _TARGET_SR,
        })
    return chunks


def probe_duration_seconds(input_bytes: bytes, input_filename: str) -> Optional[float]:
    """Cheap duration probe using the same decoder. None if decode fails.

    Use this for pre-chunk billing/metering or to decide whether to chunk at all.
    """
    try:
        pcm = _decode_to_pcm16_mono_16k(input_bytes, input_filename)
    except ValueError:
        return None
    return len(pcm) / _TARGET_SR if len(pcm) else 0.0
