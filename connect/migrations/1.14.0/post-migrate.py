# -*- coding: utf-8 -*-
"""Post-migrate for 1.14.0.

pre-migrate converted text fields to connect.audio rows and set the m2o
columns. Three follow-ups happen here, AFTER data/audio.xml has loaded
(so the 5 new Polly Generative voice rows exist):

1. Carry connect_settings.system_voice (legacy Selection) forward into
   default_twilio_voice (Many2one). Runs here because the Generative voice
   rows — which many operators had selected — are only reachable once data
   files have loaded.
2. Pin callflow.voice onto the migrated prompt/invalid/voicemail audios
   by looking up connect.voice.external_id. Preserves the per-callflow
   voice operators had configured before 1.14.0, otherwise every migrated
   audio speaks in settings.default_twilio_voice.
3. Rebuild the audio reference graph + BFS reachability. pre-migrate wired
   m2o columns via raw SQL which bypasses the referrer mixin, so both are
   stale until rebuilt here.
"""

import logging

from odoo import api, SUPERUSER_ID

logger = logging.getLogger(__name__)


CALLFLOW_AUDIO_FIELDS = (
    'prompt_audio_id', 'invalid_input_audio_id', 'voicemail_audio_id',
    'after_hours_audio_id',
)


def _column_exists(cr, table, column):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = %s AND column_name = %s
    """, (table, column))
    return bool(cr.fetchone())


def _carry_system_voice(cr, env):
    """Move connect_settings.system_voice -> default_twilio_voice.

    Matches old Selection value against connect_voice.external_id. If any
    settings row has a system_voice value that doesn't resolve, log ERROR
    with the unresolved value and leave the old column so operators can
    recover manually. Never silently destroy the preference.
    """
    if not _column_exists(cr, 'connect_settings', 'system_voice'):
        return
    cr.execute("""
        UPDATE connect_settings s
           SET default_twilio_voice = v.id
          FROM connect_voice v
         WHERE s.default_twilio_voice IS NULL
           AND s.system_voice IS NOT NULL
           AND v.provider = 'twilio'
           AND v.external_id = s.system_voice
    """)
    if cr.rowcount:
        logger.info(
            'Migrated system_voice -> default_twilio_voice on %d '
            'settings row(s).', cr.rowcount)
    cr.execute("""
        SELECT id, system_voice FROM connect_settings
         WHERE system_voice IS NOT NULL
           AND default_twilio_voice IS NULL
    """)
    unresolved = cr.fetchall()
    if unresolved:
        for sid, val in unresolved:
            logger.error(
                'connect_settings#%s.system_voice=%r has no matching '
                'connect.voice row (provider=twilio, external_id=%r). '
                'Leaving system_voice column in place so the operator can '
                'recover the value manually — set default_twilio_voice in '
                'the Connect settings, then drop the column.',
                sid, val, val)
        return
    cr.execute('ALTER TABLE connect_settings DROP COLUMN system_voice')
    logger.info('Dropped connect_settings.system_voice.')


def _pin_callflow_voices(env):
    """For each callflow, pin its legacy `voice` string onto the migrated
    audios so operators don't lose their per-flow voice choice.

    Only touches audios where use_default_voice=True — skipping audios the
    operator has already explicitly customised. Idempotent: re-running
    applies the same pins without drift.
    """
    Callflow = env['connect.callflow'].sudo()
    Voice = env['connect.voice'].sudo()
    callflows = Callflow.search([('voice', '!=', False)])
    pinned = 0
    for cf in callflows:
        voice = Voice.search([
            ('provider', '=', 'twilio'),
            ('external_id', '=', cf.voice),
        ], limit=1)
        if not voice:
            continue
        for field_name in CALLFLOW_AUDIO_FIELDS:
            audio = cf[field_name]
            if audio and audio.use_default_voice:
                audio.write({
                    'voice_id': voice.id,
                    'use_default_voice': False,
                })
                pinned += 1
    if pinned:
        logger.info(
            'Pinned connect.voice on %d migrated callflow audio(s) from '
            'legacy per-callflow voice strings.', pinned)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    _carry_system_voice(cr, env)
    _pin_callflow_voices(env)
    audios = env['connect.audio'].sudo().search([])
    if audios:
        audios._refresh_references()
        logger.info('Refreshed references for %d audio(s).', len(audios))
    env['connect.audio'].sudo()._refresh_reachability()
    logger.info('Reachability BFS complete.')
