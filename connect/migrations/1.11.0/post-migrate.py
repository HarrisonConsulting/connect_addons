"""Post-migrate for 1.11.0:

connect.audio now preserves the uploaded master WAV verbatim and derives
PSTN-format bytes (8 kHz μ-law WAV) lazily into connect.audio.utterance on
first render(). Previously the master was transcoded in place, destroying
the wideband upload irrecoverably.

This migration:
  1. Does NOT touch connect_audio.recording_file. Masters preserved.
  2. Normalizes connect_audio.recording_mimetype variants to 'audio/wav'.
  3. Flushes stale source='record' utterances so the new derivation path
     regenerates with correctly-resampled content on next play.

Safe to re-run: both UPDATE and DELETE are idempotent on already-normalised
state.
"""

import logging

logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        UPDATE connect_audio
           SET recording_mimetype = 'audio/wav'
         WHERE recording_mimetype IN ('audio/x-wav', 'audio/wave')
    """)
    if cr.rowcount:
        logger.info(
            'Normalized recording_mimetype on %d audio row(s).', cr.rowcount)

    cr.execute("""
        DELETE FROM connect_audio_utterance WHERE source_used = 'record'
    """)
    if cr.rowcount:
        logger.info(
            'Flushed %d stale record-source utterance(s); they will '
            'regenerate from masters on next render.', cr.rowcount)
