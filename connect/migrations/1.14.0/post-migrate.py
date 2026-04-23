# -*- coding: utf-8 -*-
"""Post-migrate for 1.14.0: text prompt/greeting fields -> connect.audio m2o.

All work lives in post-migrate because pre-migrate runs before the module
being upgraded has had its Python code loaded into the registry —
env['connect.audio'] is unresolvable there. By the time post-migrate
fires, Odoo has:
  - loaded connect.audio + the new m2o fields on user/callflow,
  - schema-synced the new columns (m2o FKs, use_default_voice,
    default_twilio_voice, active on user/callflow),
  - loaded data/audio.xml so the new Polly Generative voice rows exist.

We then:
  1. Convert legacy text fields to connect.audio rows and wire the m2o.
     Rows whose m2o is already populated (prior connect_elevenlabs sync)
     are skipped — the operator's existing choice wins.
  2. Drop the now-orphan text columns.
  3. Carry settings.system_voice (Selection) into default_twilio_voice
     (Many2one). Operators who had a Polly Generative voice selected are
     resolvable now that data/audio.xml has loaded.
  4. Pin per-callflow `voice` strings onto migrated audios so operators
     don't lose their per-flow voice choice; audios with use_default_voice
     already False (operator customised) are left alone.
  5. Refresh the audio reference graph + BFS reachability. The raw SQL
     UPDATE of m2o columns in step 1 bypasses the referrer mixin, so both
     maps are stale until rebuilt here.

Jinja voicemail text (`{{user.name}}`) is converted to the new `{name}`
token form via Audio.jinja_to_token when possible. Unconvertible Jinja
is pre-rendered once through a vanilla jinja2.Environment; if rendering
itself throws, the raw template is stored with a `[MIGRATION FAILED]`
prefix so the failure is audible on the next call rather than silently
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


def _default_source_and_voice(env):
    """Resolve (source, voice_id-or-None) for newly-created audios.

    Delegates to settings.get_default_audio_source() so the elevenlabs
    extension can steer new audios to elevenlabs_tts when enabled.
    """
    source, voice = env['connect.settings'].sudo().get_default_audio_source()
    return source, (voice.id if voice else False)


def _jinja_fallback_render(env, root_var, model_name, row_id, text):
    """Render legacy Jinja once to freeze the spoken output.

    On render failure keep the raw template but prefix with
    '[MIGRATION FAILED]' so the operator hears an obvious signal on the
    next call instead of a literal `{{user.name}}`.
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


def _convert_text_fields(cr, env):
    """Walk every (user|callflow, text_field) pair, materialise each
    non-empty text value as a connect.audio row, and write the id into
    the corresponding m2o column. Drops the text column at the end."""
    Audio = env['connect.audio'].sudo()
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


def _carry_system_voice(cr):
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
    operator has already explicitly customised. Idempotent.
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
    _convert_text_fields(cr, env)
    _carry_system_voice(cr)
    _pin_callflow_voices(env)
    audios = env['connect.audio'].sudo().search([])
    if audios:
        audios._refresh_references()
        logger.info('Refreshed references for %d audio(s).', len(audios))
    env['connect.audio'].sudo()._refresh_reachability()
    logger.info('Reachability BFS complete.')
