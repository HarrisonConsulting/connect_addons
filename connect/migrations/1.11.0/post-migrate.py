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
  4. Syncs active=False for rows that were already in state='archived'.
  5. Rebuilds the audio reference map for all active audios.
  6. Runs the reachability BFS so is_reachable flags are correct on install.
  7. Promotes reachable draft audios to live so the list doesn't look stale.

Safe to re-run: all SQL and ORM operations are idempotent on already-normalised
state.
"""

import logging

from odoo import api, SUPERUSER_ID

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

    # Sync the new active column: rows that were archived via the old state
    # machine have state='archived' but active defaulted to True on column add.
    cr.execute("""
        UPDATE connect_audio SET active = FALSE WHERE state = 'archived'
    """)
    if cr.rowcount:
        logger.info(
            'Set active=FALSE on %d previously-archived audio row(s).',
            cr.rowcount)

    env = api.Environment(cr, SUPERUSER_ID, {})

    # ── Bootstrap connect_elevenlabs audio M2Os on existing rows ──────────
    # connect_elevenlabs adds prompt_audio_id / invalid_input_audio_id /
    # voicemail_audio_id to callflow and greeting_audio_id / voicemail_audio_id
    # to user.  Its _sync_audio_fields() fires on create/write but was never
    # called retroactively — rows that existed before the extension was
    # installed have all audio M2Os NULL.  Populate them now so the reference
    # map and reachability BFS below include the bootstrapped records.
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'connect_callflow'
           AND column_name = 'prompt_audio_id'
    """)
    if cr.fetchone():
        callflows = env['connect.callflow'].sudo().search([
            '|', '|',
            '&', ('prompt_message', '!=', False), ('prompt_audio_id', '=', False),
            '&', ('invalid_input_message', '!=', False), ('invalid_input_audio_id', '=', False),
            '&', ('voicemail_enabled', '=', True),
                 '&', ('voicemail_prompt', '!=', False), ('voicemail_audio_id', '=', False),
        ])
        if callflows:
            callflows._sync_audio_fields()
            logger.info(
                'Bootstrapped connect.audio records for %d callflow(s).', len(callflows))

    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'connect_user'
           AND column_name = 'greeting_audio_id'
    """)
    if cr.fetchone():
        users = env['connect.user'].sudo().search([
            '|',
            '&', ('greeting_message', '!=', False), ('greeting_audio_id', '=', False),
            '&', ('voicemail_enabled', '=', True),
                 '&', ('voicemail_prompt', '!=', False), ('voicemail_audio_id', '=', False),
        ])
        if users:
            users._sync_audio_fields()
            logger.info(
                'Bootstrapped connect.audio records for %d user(s).', len(users))

    # Rebuild the reference map so Where Used and state reconciliation are
    # accurate before the reachability BFS reads them.
    audios = env['connect.audio'].sudo().search([])
    if audios:
        audios._refresh_references()
        logger.info('Refreshed references for %d audio(s).', len(audios))

    # Run the full routing-graph BFS so is_reachable flags are populated.
    env['connect.audio'].sudo()._refresh_reachability()
    logger.info('Reachability BFS complete.')

    # Promote reachable draft audios to live — a draft audio that is actually
    # reachable from a DID or running campaign is clearly in use and should be
    # live, not stale in draft.
    to_promote = env['connect.audio'].sudo().search([
        ('state', '=', 'draft'),
        ('is_reachable', '=', True),
    ])
    if to_promote:
        to_promote.write({'state': 'live'})
        logger.info(
            'Promoted %d reachable draft audio(s) to live.', len(to_promote))
