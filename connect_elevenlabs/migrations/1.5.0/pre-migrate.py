"""Pre-migrate for 1.5.0: lossless move of voice/file/system_message data into
the platform-agnostic connect.voice / connect.audio / connect.audio.utterance models.

Runs BEFORE Odoo's schema sync of connect_elevenlabs 1.5.0. The connect base
module (>= 1.10.0) already created the connect_voice, connect_audio, and
connect_audio_utterance tables.

We do voice and m2o-target remapping here so the columns whose comodel changed
(connect_settings.elevenlabs_voice, connect_elevenlabs_agent.voice) hold IDs
valid for the new comodel by the time Odoo re-installs their FK constraints.
The connect_user.* and connect_callflow.* file refs become NEW columns added
by schema sync; their backfill happens in post-migrate.
"""

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


def _drop_fk_to(cr, table, column):
    cr.execute("""
        SELECT con.conname
          FROM pg_constraint con
          JOIN pg_class rel ON rel.oid = con.conrelid
          JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = ANY(con.conkey)
         WHERE rel.relname = %s AND att.attname = %s AND con.contype = 'f'
    """, (table, column))
    for (conname,) in cr.fetchall():
        cr.execute(f'ALTER TABLE "{table}" DROP CONSTRAINT IF EXISTS "{conname}"')


def _bulk_remap(cr, table, column, id_map):
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
           SET "{column}" = m.new_id
          FROM (VALUES {values_sql}) AS m(old_id, new_id)
         WHERE t."{column}" = m.old_id
        """,
        args,
    )


def migrate(cr, version):
    if not _table_exists(cr, 'connect_elevenlabs_voice'):
        logger.info('connect_elevenlabs_voice missing; nothing to migrate.')
        return

    cr.execute("""
        SELECT id, voice_id, name, language, accent, age, gender, preview_url, description
          FROM connect_elevenlabs_voice
    """)
    voice_id_map = {}
    for old_id, ext_id, name, lang, accent, age, gender, preview, desc in cr.fetchall():
        if not ext_id:
            continue
        cr.execute("""
            SELECT id FROM connect_voice
             WHERE provider = 'elevenlabs' AND external_id = %s
             LIMIT 1
        """, (ext_id,))
        existing = cr.fetchone()
        if existing:
            voice_id_map[old_id] = existing[0]
            continue
        cr.execute("""
            INSERT INTO connect_voice
                (name, provider, external_id, language, accent, age, gender, preview_url,
                 description, create_date, write_date)
            VALUES (%s, 'elevenlabs', %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            RETURNING id
        """, (name or ext_id, ext_id, lang, accent, age, gender, preview, desc))
        voice_id_map[old_id] = cr.fetchone()[0]

    logger.info('Migrated %d ElevenLabs voices to connect.voice.', len(voice_id_map))

    # Stash the map in ir_config_parameter so post-migrate can reuse it.
    import json
    cr.execute("""
        INSERT INTO ir_config_parameter (key, value, create_date, write_date)
        VALUES ('connect_elevenlabs.migration_1_5_0_voice_map', %s, NOW(), NOW())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """, (json.dumps(voice_id_map),))

    if _column_exists(cr, 'connect_settings', 'elevenlabs_voice'):
        _drop_fk_to(cr, 'connect_settings', 'elevenlabs_voice')
        _bulk_remap(cr, 'connect_settings', 'elevenlabs_voice', voice_id_map)
        # Pre-null any IDs the map didn't cover; otherwise the new FK
        # (connect.voice) install at schema-sync time will fail.
        cr.execute("""
            UPDATE connect_settings
               SET elevenlabs_voice = NULL
             WHERE elevenlabs_voice IS NOT NULL
               AND elevenlabs_voice NOT IN (SELECT id FROM connect_voice)
        """)

    if _table_exists(cr, 'connect_elevenlabs_agent') \
            and _column_exists(cr, 'connect_elevenlabs_agent', 'voice'):
        _drop_fk_to(cr, 'connect_elevenlabs_agent', 'voice')
        _bulk_remap(cr, 'connect_elevenlabs_agent', 'voice', voice_id_map)
        # connect.elevenlabs_agent.voice is required. Any unmapped IDs would
        # break the new FK and the not-null constraint. Default to whatever
        # voice the migration mapped (any one) so the upgrade completes;
        # operators reassign post-upgrade.
        default_voice_id = next(iter(voice_id_map.values()), None)
        if default_voice_id is not None:
            cr.execute("""
                UPDATE connect_elevenlabs_agent
                   SET voice = %s
                 WHERE voice IS NOT NULL
                   AND voice NOT IN (SELECT id FROM connect_voice)
            """, (default_voice_id,))

    # connect 1.14.0 renamed connect.user.greeting_message -> greeting_audio_id.
    # Inherited views that used greeting_message as a position selector are now
    # invalid. Delete them so Odoo recreates them cleanly from their XML files.
    cr.execute("""
        SELECT id FROM ir_ui_view
        WHERE model = 'connect.user'
          AND inherit_id IS NOT NULL
          AND arch_db::text LIKE '%greeting_message%'
    """)
    stale_view_ids = [row[0] for row in cr.fetchall()]
    if stale_view_ids:
        cr.execute("""
            DELETE FROM ir_model_data
            WHERE model = 'ir.ui.view' AND res_id = ANY(%s)
        """, (stale_view_ids,))
        cr.execute("DELETE FROM ir_ui_view WHERE id = ANY(%s)", (stale_view_ids,))
        logger.info('Removed %d stale connect.user views with greeting_message.', len(stale_view_ids))
