"""Post-migrate for 1.5.0:
- Convert connect.elevenlabs_file rows to connect.audio + connect.audio.utterance.
- Convert connect.elevenlabs_system_message rows to connect.audio.utterance attached
  to whichever connect.audio already owns the matching system_key (seeded by base
  connect 1.10.0). Never overwrite the seeded audio's source/voice — the user
  enables ElevenLabs by switching the audio's source explicitly.
- Backfill the new connect.user / connect.callflow audio_id columns from the
  legacy *_file columns via the file id map.
- Re-point ir.attachment rows that backed connect.elevenlabs_file binaries to
  the new connect.audio.utterance rows.

Note: connect.elevenlabs_system_message uses _inherit (not _inherits) with a
distinct _name, so it owns its own table with a copy of file/text/filename
columns. Read it directly — never JOIN to connect_elevenlabs_file by id.
"""

import json
import logging

logger = logging.getLogger(__name__)


def _table_exists(cr, table):
    cr.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
        (table,),
    )
    return bool(cr.fetchone())


def _column_exists(cr, table, column):
    cr.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
        (table, column),
    )
    return bool(cr.fetchone())


def _bulk_remap(cr, table, source_col, target_col, id_map):
    if not id_map:
        return
    placeholders = []
    args = []
    for old_id, new_id in id_map.items():
        placeholders.append('(%s, %s)')
        args.extend([old_id, new_id])
    values_sql = ', '.join(placeholders)
    cr.execute(
        f"""
        UPDATE "{table}" t
           SET "{target_col}" = m.new_id
          FROM (VALUES {values_sql}) AS m(old_id, new_id)
         WHERE t."{source_col}" = m.old_id
        """,
        args,
    )


def migrate(cr, version):
    cr.execute("""
        SELECT value FROM ir_config_parameter
         WHERE key = 'connect_elevenlabs.migration_1_5_0_voice_map'
    """)
    row = cr.fetchone()
    voice_id_map = {int(k): v for k, v in json.loads(row[0]).items()} if row else {}
    default_voice_id = next(iter(voice_id_map.values()), None)

    file_id_map = {}  # old elevenlabs_file.id -> new connect_audio.id

    # ------------------------------------------------------------------
    # 1. System messages: attach a utterance to whichever connect.audio
    #    already owns the system_key (seeded by base in audio.xml). Never
    #    overwrite the seed — the operator opts into ElevenLabs by editing
    #    the audio's source explicitly.
    # ------------------------------------------------------------------
    if _table_exists(cr, 'connect_elevenlabs_system_message'):
        cr.execute("""
            SELECT id, message_key, text, filename
              FROM connect_elevenlabs_system_message
        """)
        for sm_id, message_key, text, filename in cr.fetchall():
            cr.execute("""
                SELECT id FROM connect_audio WHERE system_key = %s LIMIT 1
            """, (message_key,))
            existing = cr.fetchone()
            if existing:
                audio_id = existing[0]
            else:
                # No seed for this key (custom one operators added by hand);
                # create a parking row so the binary is not lost.
                cr.execute("""
                    INSERT INTO connect_audio
                        (name, system_key, source, voice_id, static_text, is_dynamic,
                         create_date, write_date)
                    VALUES (%s, %s, 'elevenlabs_tts', %s, %s, FALSE, NOW(), NOW())
                    RETURNING id
                """, (f'System: {message_key}', message_key, default_voice_id, text))
                audio_id = cr.fetchone()[0]
            _create_utterance_from_legacy(cr, audio_id, default_voice_id, text, sm_id,
                                          legacy_table='connect.elevenlabs_system_message')

    # ------------------------------------------------------------------
    # 2. Generic files: connect.elevenlabs_file -> connect.audio + utterance
    # ------------------------------------------------------------------
    if _table_exists(cr, 'connect_elevenlabs_file'):
        cr.execute("""
            SELECT id, text, filename
              FROM connect_elevenlabs_file
        """)
        for file_id, text, filename in cr.fetchall():
            label = (text or '')[:60] or f'Legacy file {file_id}'
            cr.execute("""
                INSERT INTO connect_audio
                    (name, source, voice_id, static_text, is_dynamic,
                     create_date, write_date)
                VALUES (%s, 'elevenlabs_tts', %s, %s, FALSE, NOW(), NOW())
                RETURNING id
            """, (label, default_voice_id, text))
            audio_id = cr.fetchone()[0]
            file_id_map[file_id] = audio_id
            _create_utterance_from_legacy(cr, audio_id, default_voice_id, text, file_id,
                                          legacy_table='connect.elevenlabs_file')

    logger.info('Migrated %d audio files to connect.audio + utterance.', len(file_id_map))

    # ------------------------------------------------------------------
    # 3. Backfill new audio_id columns on connect_user / connect_callflow
    #    from the legacy *_file columns, then drop the legacy columns.
    # ------------------------------------------------------------------
    user_renames = [
        ('greeting_message_file', 'greeting_audio_id'),
        ('voicemail_prompt_file', 'voicemail_audio_id'),
    ]
    callflow_renames = [
        ('prompt_message_file', 'prompt_audio_id'),
        ('invalid_input_message_file', 'invalid_input_audio_id'),
        ('voicemail_prompt_file', 'voicemail_audio_id'),
    ]

    if file_id_map:
        for src, dst in user_renames:
            if _column_exists(cr, 'connect_user', src) and _column_exists(cr, 'connect_user', dst):
                _bulk_remap(cr, 'connect_user', src, dst, file_id_map)
        for src, dst in callflow_renames:
            if _column_exists(cr, 'connect_callflow', src) and _column_exists(cr, 'connect_callflow', dst):
                _bulk_remap(cr, 'connect_callflow', src, dst, file_id_map)

    for src, _ in user_renames:
        if _column_exists(cr, 'connect_user', src):
            cr.execute(f'ALTER TABLE connect_user DROP COLUMN IF EXISTS "{src}" CASCADE')
    for src, _ in callflow_renames:
        if _column_exists(cr, 'connect_callflow', src):
            cr.execute(f'ALTER TABLE connect_callflow DROP COLUMN IF EXISTS "{src}" CASCADE')

    # _bulk_remap above touched audio m2o columns via raw SQL, which
    # bypasses the referrer mixin. Refresh reachability so Where-Used /
    # is_reachable reflect the post-remap graph.
    #
    # Note: the 1.5.0-era `_sync_audio_fields` re-sync was removed in
    # connect 1.14.0 when the text↔audio sync was retired — connect's
    # 1.14.0 pre-migrate is the authoritative text→audio converter and
    # handles every previously-unsynced row.
    try:
        from odoo import api, SUPERUSER_ID
        env = api.Environment(cr, SUPERUSER_ID, {})
        env['connect.audio']._refresh_reachability()
    except Exception as e:
        logger.warning('Post-migrate reachability refresh skipped: %s', e)

    cr.execute("""
        DELETE FROM ir_config_parameter
         WHERE key = 'connect_elevenlabs.migration_1_5_0_voice_map'
    """)


def _create_utterance_from_legacy(cr, audio_id, voice_id, text, legacy_id, legacy_table):
    """Create a utterance and re-point the legacy binary attachment to it.

    Seeds params/params_hash to match what the elevenlabs renderer would
    produce for these legacy rows, so the cache lookup hits on first playback
    instead of regenerating.
    """
    import hashlib
    import json
    text_hash = hashlib.sha256((text or '').encode('utf-8')).hexdigest()

    voice_external_id = None
    if voice_id is not None:
        cr.execute('SELECT external_id FROM connect_voice WHERE id = %s', (voice_id,))
        row = cr.fetchone()
        if row:
            voice_external_id = row[0]
    params = {
        'model_id': 'eleven_multilingual_v2',
        'voice_external_id': voice_external_id,
    }
    params_json = json.dumps(params, sort_keys=True, separators=(',', ':'))
    params_hash = hashlib.sha256(params_json.encode('utf-8')).hexdigest()

    cr.execute("""
        INSERT INTO connect_audio_utterance
            (audio_id, voice_id, rendered_text, text_hash, params, params_hash,
             mimetype, source_used, generated_on, create_date, write_date)
        VALUES (%s, %s, %s, %s, %s, %s, 'audio/mpeg', 'elevenlabs_tts', NOW(), NOW(), NOW())
        ON CONFLICT (audio_id, voice_id, text_hash, params_hash) DO UPDATE
          SET rendered_text = EXCLUDED.rendered_text
        RETURNING id
    """, (audio_id, voice_id, text, text_hash, params_json, params_hash))
    utterance_id = cr.fetchone()[0]

    cr.execute("""
        UPDATE ir_attachment
           SET res_model = 'connect.audio.utterance', res_field = 'file', res_id = %s
         WHERE res_model = %s
           AND res_field = 'file'
           AND res_id = %s
    """, (utterance_id, legacy_table, legacy_id))
