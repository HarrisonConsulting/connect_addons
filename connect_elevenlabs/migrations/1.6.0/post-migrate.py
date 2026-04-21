"""Post-migrate for 1.6.0:

The synthesis param surface expanded — previously only model_id and
voice_external_id scoped the cache. Now stability, similarity_boost, style,
speaker_boost, and (when voice.language is set) language are also in
params_hash. Existing utterances were generated under the old default
params; their cached params_hash values are stale.

Rather than blindly seed new params_hash values (which would claim the
old audio bytes match today's defaults — they don't, we changed the
defaults), delete the ElevenLabs-sourced utterances so next playback
regenerates them under the new params. Operators who want to preserve
their old voice character can copy the previous defaults into settings
before upgrading.
"""

import logging

logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        DELETE FROM connect_audio_utterance
         WHERE source_used = 'elevenlabs_tts'
    """)
    logger.info(
        'Cleared %d ElevenLabs utterance(s); will regenerate on next playback.',
        cr.rowcount)
