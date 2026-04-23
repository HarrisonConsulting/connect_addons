# -*- coding: utf-8 -*-
"""Pre-migrate for 1.14.0: text prompt/greeting fields -> connect.audio m2o.

Converts in place:
  connect_user.voicemail_prompt    (Text, Jinja) -> voicemail_audio_id
  connect_user.greeting_message    (Char)        -> greeting_audio_id
  connect_callflow.prompt_message         (Text) -> prompt_audio_id
  connect_callflow.invalid_input_message  (Text) -> invalid_input_audio_id
  connect_callflow.voicemail_prompt       (Text) -> voicemail_audio_id

The m2o columns may already exist (when connect_elevenlabs was previously
installed it declared them; migrations run before ORM schema sync). Rows
whose m2o is already populated are skipped so the operator's prior choice
via the elevenlabs sync hook wins.

All created audios start with use_default_voice=True. The DB-wide default
(settings.default_twilio_voice) is carried forward from the old
system_voice Selection in POST-migrate, because (a) the new Polly
Generative voice rows are loaded by data/audio.xml between pre- and
post-migrate, and (b) per-callflow voice pinning also resolves against
those voices.

Jinja voicemail text (`{{user.name}}`) is converted to the new `{name}`
token form via Audio.jinja_to_token when possible. Unconvertible Jinja is
pre-rendered once through a vanilla jinja2.Environment. If pre-rendering
itself throws, the raw template is stored with a `[MIGRATION FAILED]`
prefix so the failure is audible on the next call instead of the audio
speaking the literal template body.
"""

import jinja2
import logging

from odoo import api, SUPERUSER_ID

logger = logging.getLogger(__name__)


# (table, text_column, audio_column, name_column, root_var_for_jinja)
USER_FIELDS = [
    ('connect_user', 'voicemail_prompt', 'voicemail_audio_id', 'username', 'user'),
    ('connect_user', 'greeting_message', 'greeting_audio_id', 'username', None),
]
CALLFLOW_FIELDS = [
    ('connect_callflow', 'prompt_message',
     'prompt_audio_id', 'name', None),
    ('connect_callflow', 'invalid_input_message',
     'invalid_input_audio_id', 'name', None),
    ('connect_callflow', 'voicemail_prompt',
     'voicemail_audio_id', 'name', None),
    ('connect_callflow', 'after_hours_message',
     'after_hours_audio_id', 'name', None),
]
ALL_FIELDS = USER_FIELDS + CALLFLOW_FIELDS


def _column_exists(cr, table, column):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = %s AND column_name = %s
    """, (table, column))
    return bool(cr.fetchone())


def _ensure_audio_column(cr, table, audio_column):
    """Create the m2o FK column if it doesn't exist yet.

    Pre-migrate runs before the ORM materialises new columns. When
    connect_elevenlabs was previously installed the column already exists; on
    fresh migrations from a pre-elevenlabs install it doesn't, and we need it
    to write ids into during the pre-migrate.

    Odoo's `fix_foreign_key` matches FKs by (column, referenced table/column,
    deltype) rather than by constraint name, so the name is cosmetic — but an
    explicit `<table>_<column>_fkey` keeps introspection tooling and migration
    diffs readable.
    """
    if _column_exists(cr, table, audio_column):
        return
    constraint = f'{table}_{audio_column}_fkey'
    cr.execute(f"""
        ALTER TABLE {table}
          ADD COLUMN {audio_column} INTEGER,
          ADD CONSTRAINT {constraint}
              FOREIGN KEY ({audio_column})
              REFERENCES connect_audio(id) ON DELETE SET NULL
    """)
    logger.info('Added column %s.%s (fk %s)', table, audio_column, constraint)


def _default_source_and_voice(env):
    """Resolve (source, voice_id-or-None) for newly-created audios.

    Delegates to settings.get_default_audio_source() so the elevenlabs
    extension can steer new audios to elevenlabs_tts when enabled.
    """
    source, voice = env['connect.settings'].sudo().get_default_audio_source()
    return source, (voice.id if voice else False)


def _jinja_fallback_render(env, root_var, model_name, row_id, text):
    """Render legacy Jinja once to freeze the spoken output.

    Returns the rendered static text. On render failure we keep the raw
    template but prefix with '[MIGRATION FAILED]' so the operator hears
    an obvious signal on the next call instead of a literal `{{user.name}}`.
    """
    try:
        record = env[model_name].sudo().browse(row_id)
        template = jinja2.Environment().from_string(text)
        return template.render({root_var: record})
    except Exception as e:
        logger.warning(
            'Jinja pre-render failed for %s#%d (text=%r): %s — '
            'storing with [MIGRATION FAILED] prefix.',
            model_name, row_id, (text or '')[:80], e)
        return f'[MIGRATION FAILED] {text}'


def _also_ensure_default_twilio_voice_column(cr):
    """Create connect_settings.default_twilio_voice early so post-migrate
    has a target to write into. The new Selection Polly Generative voice
    rows only appear after data/audio.xml loads between pre- and
    post-migrate, so the value carry-over itself is deferred."""
    if _column_exists(cr, 'connect_settings', 'default_twilio_voice'):
        return
    cr.execute("""
        ALTER TABLE connect_settings
          ADD COLUMN default_twilio_voice INTEGER,
          ADD CONSTRAINT connect_settings_default_twilio_voice_fkey
              FOREIGN KEY (default_twilio_voice)
              REFERENCES connect_voice(id) ON DELETE SET NULL
    """)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Audio = env['connect.audio'].sudo()

    for table, _text, audio_col, _name, _root in ALL_FIELDS:
        _ensure_audio_column(cr, table, audio_col)
    _also_ensure_default_twilio_voice_column(cr)

    default_source, default_voice_id = _default_source_and_voice(env)
    model_by_table = {
        'connect_user': 'connect.user',
        'connect_callflow': 'connect.callflow',
    }

    for table, text_col, audio_col, name_col, root_var in ALL_FIELDS:
        if not _column_exists(cr, table, text_col):
            continue
        model_name = model_by_table[table]
        model_row = env['ir.model'].sudo().search(
            [('model', '=', model_name)], limit=1)

        cr.execute(f"""
            SELECT id, {name_col}, {text_col}
              FROM {table}
             WHERE {text_col} IS NOT NULL
               AND {text_col} != ''
               AND ({audio_col} IS NULL)
        """)
        rows = cr.fetchall()
        logger.info(
            '%s.%s: %d row(s) to convert.', table, text_col, len(rows))
        for row_id, row_name, text in rows:
            is_dynamic = False
            static_text = text
            model_id = False
            if root_var:
                converted, ok = Audio.jinja_to_token(text, root_var=root_var)
                if ok and converted != text:
                    is_dynamic = True
                    static_text = converted
                    model_id = model_row.id
                elif not ok:
                    static_text = _jinja_fallback_render(
                        env, root_var, model_name, row_id, text)

            audio = Audio.create({
                'name': f'{row_name or model_name} {text_col}',
                'source': default_source,
                'voice_id': default_voice_id,
                'use_default_voice': True,
                'static_text': static_text,
                'is_dynamic': is_dynamic,
                'model_id': model_id,
            })
            cr.execute(
                f'UPDATE {table} SET {audio_col} = %s WHERE id = %s',
                (audio.id, row_id))
        cr.execute(f'ALTER TABLE {table} DROP COLUMN {text_col}')
        logger.info('Dropped %s.%s after conversion.', table, text_col)
