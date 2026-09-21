"""Forward-migrate monolithic Twilio models onto connect_twilio.

Renames tables and re-owns ir.model / ir.model.fields / xmlids so NG
connect_twilio can claim them. Never DROP. Never archive to `_*_legacy`.
Exact allowlists only — `_` is a SQL wildcard, so nothing is matched by
prefix. Join ir_model_fields on (model, name).
"""

import logging

_logger = logging.getLogger(__name__)

NEW_MODULE = 'connect_twilio'
OLD_MODULE = 'connect'
PBX_MODULE = 'connect_pbx'
COUNTS_TABLE = '_connect_ng_cutover_counts'

KEEP_TABLES = (
    'connect_call',
    'connect_user',
    'connect_recording',
    'connect_channel',
    'connect_message',
    'connect_schedule',
    'connect_settings',
    'connect_favorite',
    'connect_debug',
)

MOVE_TABLES = (
    ('connect_exten', 'connect_twilio_exten'),
    ('connect_callflow', 'connect_twilio_callflow'),
    ('connect_callflow_choice', 'connect_twilio_callflow_choice'),
    ('connect_number', 'connect_twilio_number'),
    ('connect_outgoing_callerid', 'connect_twilio_outgoing_callerid'),
    ('connect_user_callflow', 'connect_twilio_user_callflow'),
    ('connect_user_callflow_call', 'connect_twilio_user_callflow_call'),
    ('connect_message_configuration', 'connect_twilio_message_configuration'),
    ('connect_domain', 'connect_twilio_domain'),
    ('connect_twiml', 'connect_twilio_twiml'),
)

MOVE_REL = (
    (
        'connect_callflow_connect_user_rel',
        'connect_twilio_callflow_connect_user_rel',
        (('connect_callflow_id', 'connect_twilio_callflow_id'),),
    ),
)

MOVE_MODELS = (
    ('connect.exten', 'connect.twilio.exten'),
    ('connect.callflow', 'connect.twilio.callflow'),
    ('connect.callflow_choice', 'connect.twilio.callflow_choice'),
    ('connect.number', 'connect.twilio.number'),
    ('connect.outgoing_callerid', 'connect.twilio.outgoing_callerid'),
    ('connect.user_callflow', 'connect.twilio.user_callflow'),
    ('connect.user_callflow_call', 'connect.twilio.user_callflow_call'),
    ('connect.message_configuration', 'connect.twilio.message_configuration'),
    ('connect.domain', 'connect.twilio.domain'),
    ('connect.twiml', 'connect.twilio.twiml'),
)

# Same `_name`, new owning module.
SAME_NAME_MODELS = (
    'connect.whatsapp_sender',
    'connect.message_content_template',
    'connect.whatsapp_composer',
)

PBX_TABLES = (
    'connect_audio',
    'connect_schedule_line',
    'connect_schedule_holiday',
    'connect_documentation',
    'connect_scheduled_call',
    'connect_voicemail_box',
    'connect_park_slot',
    'connect_conversation',
)

COUNT_KPI = (
    'connect_call',
    'connect_user',
    'connect_number',
    'connect_recording',
)

# Columns on KEEP connect_user that NG connect_twilio renamed.
USER_COLUMN_RENAMES = (
    ('exten', 'twilio_exten'),
    ('exten_number', 'twilio_exten_number'),
    ('outgoing_callerid', 'twilio_outgoing_callerid'),
)

# Fields on KEEP models that connect_twilio now declares.
TWILIO_KEEP_FIELDS = (
    ('connect.call', 'call_sid'),
    ('connect.call', 'price'),
    ('connect.call', 'price_unit'),
    ('connect.call', 'price_currency'),
    ('connect.call', 'is_price_fetched'),
    ('connect.user', 'username'),
    ('connect.user', 'sid'),
    ('connect.user', 'password'),
    ('connect.user', 'domain'),
    ('connect.user', 'sip_enabled'),
    ('connect.user', 'sip_priority'),
    ('connect.user', 'client_enabled'),
    ('connect.user', 'client_priority'),
    ('connect.user', 'sip_ring_timeout'),
    ('connect.user', 'client_ring_timeout'),
    ('connect.user', 'uri'),
    ('connect.user', 'connect_uri'),
    ('connect.user', 'application'),
    ('connect.user', 'whatsapp_sender_id'),
    ('connect.user', 'twilio_edge'),
    ('connect.user', 'twilio_exten'),
    ('connect.user', 'twilio_exten_number'),
    ('connect.user', 'twilio_outgoing_callerid'),
    ('connect.settings', 'account_sid'),
    ('connect.settings', 'auth_token'),
    ('connect.settings', 'display_auth_token'),
    ('connect.settings', 'twilio_api_key'),
    ('connect.settings', 'twilio_api_secret'),
    ('connect.settings', 'display_twilio_api_secret'),
    ('connect.settings', 'twilio_balance'),
    ('connect.settings', 'twilio_region'),
    ('connect.settings', 'twilio_edge'),
    ('connect.settings', 'twilio_auto_sync'),
    ('connect.settings', 'twilio_verify_requests'),
    ('connect.settings', 'fetch_call_prices'),
    ('connect.message', 'message_sid'),
    ('connect.message', 'account_sid'),
    ('connect.message', 'messaging_service_sid'),
)

# Fields that stay on a renamed model but are declared by connect_pbx.
PBX_MOVE_FIELDS = (
    ('connect.twilio.callflow', 'prompt_audio_id'),
    ('connect.twilio.callflow', 'prompt_preview'),
    ('connect.twilio.callflow', 'invalid_input_audio_id'),
    ('connect.twilio.callflow', 'invalid_input_preview'),
    ('connect.twilio.callflow', 'voicemail_audio_id'),
    ('connect.twilio.callflow', 'voicemail_preview'),
    ('connect.twilio.callflow', 'voicemail_box_id'),
    ('connect.twilio.callflow', 'after_hours_audio_id'),
    ('connect.twilio.callflow', 'after_hours_preview'),
    ('connect.twilio.callflow', 'schedule_id'),
    ('connect.twilio.callflow', 'business_hours_enabled'),
    ('connect.twilio.callflow', 'business_hours_start'),
    ('connect.twilio.callflow', 'business_hours_end'),
    ('connect.twilio.callflow', 'business_hours_timezone'),
    ('connect.twilio.callflow', 'after_hours_voicemail'),
    ('connect.twilio.callflow', 'active'),
    ('connect.twilio.twiml', 'referenced_audio_ids'),
    ('connect.call', 'voicemail_box_id'),
    ('connect.call', 'voicemail_stage_id'),
    ('connect.message', 'conversation_id'),
    ('connect.schedule', 'line_ids'),
    ('connect.schedule', 'holiday_ids'),
    ('connect.settings', 'default_twilio_voice'),
    ('connect.settings', 'park_hold_music_audio_id'),
    ('connect.settings', 'park_hold_music_audio_id_source'),
    ('connect.settings', 'last_reachability_refresh_on'),
    ('connect.settings', 'voicemail_max_length'),
    ('connect.settings', 'voicemail_finish_key'),
    ('connect.settings', 'park_slot_count'),
    ('connect.settings', 'park_timeout'),
    ('connect.settings', 'park_announcement_enabled'),
    ('connect.settings', 'pronunciation_rules'),
    ('connect.user', 'dnd_enabled'),
    ('connect.user', 'greeting_audio_id'),
    ('connect.user', 'greeting_preview'),
    ('connect.user', 'presence_status'),
    ('connect.user', 'presence_updated'),
    ('connect.user', 'voicemail_audio_id'),
    ('connect.user', 'voicemail_box_id'),
    ('connect.user', 'voicemail_preview'),
    ('connect.user', 'voicemail_email_enabled'),
    ('connect.user', 'callerid_number'),
)

# xmlids that stayed in connect but were renamed by NG.
RENAME_XMLIDS = (
    ('group_connect_admin', 'group_admin'),
    ('group_connect_user', 'group_user'),
    ('group_connect_webhook', 'group_webhook'),
    ('module_connect_category', 'module_category_connect'),
    ('connect_top_menu', 'menu_connect_root'),
    ('connect_settings_menu', 'menu_connect_settings'),
    ('user_list', 'view_connect_user_tree'),
    ('connect_user_form', 'view_connect_user_form'),
    ('connect_call_form', 'view_connect_call_form'),
    ('connect_call_list', 'view_connect_call_tree'),
    ('view_connect_sms_message_form', 'view_connect_message_form'),
    ('view_connect_sms_message_tree', 'view_connect_message_tree'),
    ('connect_recording_form', 'view_connect_recording_form'),
    ('connect_recording_list', 'view_connect_recording_tree'),
)

# xmlids that moved to connect_twilio under the same name.
MOVED_XMLID_NAMES = (
    'fetch_call_prices',
    'domain_route_call',
    'twiml_reject',
    'twiml_connection_failed',
    'voice_call_request',
    'action_connect_whatsapp_sender',
    'action_connect_whatsapp_composer',
    'action_connect_message_content_template',
    'view_sms_composer_form_connect',
    'access_sms_composer_user',
)

EXTEN_MODEL_REMAP = (
    ('connect.callflow', 'connect.twilio.callflow'),
    ('connect.twiml', 'connect.twilio.twiml'),
    ('callflow', 'connect.twilio.callflow'),
    ('twiml', 'connect.twilio.twiml'),
)


def _table_exists(cr, table):
    cr.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
        (table,),
    )
    return bool(cr.fetchone())


def _column_exists(cr, table, column):
    cr.execute(
        """
        SELECT 1 FROM information_schema.columns
         WHERE table_name = %s AND column_name = %s
        """,
        (table, column),
    )
    return bool(cr.fetchone())


def _count(cr, table):
    if not _table_exists(cr, table):
        return None
    cr.execute('SELECT COUNT(*) FROM "%s"' % table)
    return cr.fetchone()[0]


def _rename_table(cr, src, dst):
    """ALTER TABLE RENAME. If dst exists: raise AssertionError. Never DROP."""
    src_exists = _table_exists(cr, src)
    dst_exists = _table_exists(cr, dst)
    if dst_exists and src_exists:
        raise AssertionError(
            'refusing to rename %s -> %s: destination already exists (never DROP)'
            % (src, dst)
        )
    if dst_exists and not src_exists:
        _logger.info('table %s already renamed to %s', src, dst)
        return
    if not src_exists:
        _logger.info('table %s absent, skipping rename to %s', src, dst)
        return
    cr.execute('ALTER TABLE "%s" RENAME TO "%s"' % (src, dst))
    _logger.info('renamed table %s -> %s', src, dst)
    seq_src = '%s_id_seq' % src
    seq_dst = '%s_id_seq' % dst
    cr.execute(
        "SELECT 1 FROM pg_class WHERE relkind = 'S' AND relname = %s",
        (seq_src,),
    )
    if cr.fetchone():
        cr.execute('ALTER SEQUENCE "%s" RENAME TO "%s"' % (seq_src, seq_dst))


def _rename_rel(cr, src, dst, colmap):
    _rename_table(cr, src, dst)
    if not _table_exists(cr, dst):
        return
    for old, new in colmap:
        if _column_exists(cr, dst, old) and not _column_exists(cr, dst, new):
            cr.execute(
                'ALTER TABLE "%s" RENAME COLUMN "%s" TO "%s"' % (dst, old, new)
            )
            _logger.info('renamed column %s.%s -> %s', dst, old, new)
        elif _column_exists(cr, dst, new):
            _logger.info('column %s.%s already renamed', dst, new)
        else:
            raise AssertionError(
                'rel column %s.%s missing after rename to %s' % (dst, old, dst)
            )


def _snapshot(cr, tables):
    cr.execute(
        """
        CREATE TABLE IF NOT EXISTS %s (
            table_name varchar NOT NULL PRIMARY KEY,
            row_count integer
        )
        """ % COUNTS_TABLE
    )
    for table in tables:
        n = _count(cr, table)
        cr.execute(
            """
            INSERT INTO %s (table_name, row_count)
            VALUES (%%s, %%s)
            ON CONFLICT (table_name) DO UPDATE SET row_count = EXCLUDED.row_count
            """ % COUNTS_TABLE,
            (table, n),
        )
        _logger.info('snapshot %s = %s', table, n)


def _rename_xmlid(cr, old, new):
    cr.execute(
        """
        SELECT res_id FROM ir_model_data
         WHERE module = %s AND name = %s AND model = 'ir.ui.view'
        """,
        (OLD_MODULE, new),
    )
    new_row = cr.fetchone()
    cr.execute(
        """
        SELECT res_id FROM ir_model_data
         WHERE module = %s AND name = %s AND model = 'ir.ui.view'
        """,
        (OLD_MODULE, old),
    )
    old_row = cr.fetchone()
    if new_row and old_row and new_row[0] != old_row[0]:
        cr.execute(
            "UPDATE ir_ui_view SET inherit_id = %s WHERE inherit_id = %s",
            (new_row[0], old_row[0]),
        )
        retargeted = cr.rowcount
        cr.execute(
            """
            UPDATE ir_model_data
               SET res_id = %s
             WHERE module = %s AND name = %s
            """,
            (new_row[0], OLD_MODULE, old),
        )
        _logger.info(
            'retargeted %s inheriting views and xmlid connect.%s -> connect.%s (%s)',
            retargeted, old, new, new_row[0],
        )
        return
    if new_row:
        return
    cr.execute(
        """
        UPDATE ir_model_data
           SET name = %s
         WHERE module = %s AND name = %s
        """,
        (new, OLD_MODULE, old),
    )
    if cr.rowcount:
        _logger.info('renamed xmlid connect.%s -> connect.%s', old, new)


def _alias_xmlid(cr, canonical, alias):
    cr.execute(
        """
        SELECT model, res_id, noupdate
          FROM ir_model_data
         WHERE module = %s AND name = %s
        """,
        (OLD_MODULE, canonical),
    )
    row = cr.fetchone()
    if not row:
        return
    cr.execute(
        """
        INSERT INTO ir_model_data
            (module, name, model, res_id, noupdate,
             create_uid, create_date, write_uid, write_date)
        SELECT %s, %s, %s, %s, %s, 1, NOW(), 1, NOW()
         WHERE NOT EXISTS (
               SELECT 1 FROM ir_model_data
                WHERE module = %s AND name = %s
         )
        """,
        (OLD_MODULE, alias, row[0], row[1], row[2], OLD_MODULE, alias),
    )


def _reown_models(cr, models, module):
    if not models:
        return
    cr.execute(
        """
        UPDATE ir_model_data d
           SET module = %s
          FROM ir_model m
         WHERE d.model = 'ir.model'
           AND d.res_id = m.id
           AND d.module = %s
           AND m.model IN %s
        """,
        (module, OLD_MODULE, tuple(models)),
    )
    _logger.info('re-owned %s ir.model rows to %s', cr.rowcount, module)


def _reown_fields_by_model(cr, models, module):
    if not models:
        return
    cr.execute(
        """
        UPDATE ir_model_data d
           SET module = %s
          FROM ir_model_fields f
         WHERE d.model = 'ir.model.fields'
           AND d.res_id = f.id
           AND d.module = %s
           AND f.model IN %s
        """,
        (module, OLD_MODULE, tuple(models)),
    )
    _logger.info('re-owned %s ir.model.fields rows to %s', cr.rowcount, module)
    cr.execute(
        """
        UPDATE ir_model_data d
           SET module = %s
          FROM ir_model_fields_selection s
          JOIN ir_model_fields f ON f.id = s.field_id
         WHERE d.model = 'ir.model.fields.selection'
           AND d.res_id = s.id
           AND d.module = %s
           AND f.model IN %s
        """,
        (module, OLD_MODULE, tuple(models)),
    )
    _logger.info('re-owned %s selection rows to %s', cr.rowcount, module)


def _reown_fields_named(cr, pairs, module):
    if not pairs:
        return
    cr.execute(
        """
        UPDATE ir_model_data d
           SET module = %s
          FROM ir_model_fields f
         WHERE d.model = 'ir.model.fields'
           AND d.res_id = f.id
           AND d.module = %s
           AND (f.model, f.name) IN %s
        """,
        (module, OLD_MODULE, tuple(pairs)),
    )
    _logger.info('re-owned %s named fields to %s', cr.rowcount, module)
    cr.execute(
        """
        UPDATE ir_model_data d
           SET module = %s
          FROM ir_model_fields_selection s
          JOIN ir_model_fields f ON f.id = s.field_id
         WHERE d.model = 'ir.model.fields.selection'
           AND d.res_id = s.id
           AND d.module = %s
           AND (f.model, f.name) IN %s
        """,
        (module, OLD_MODULE, tuple(pairs)),
    )
    _logger.info('re-owned %s named selections to %s', cr.rowcount, module)


def _rename_auto_xmlids(cr, old_model, new_model):
    """Rename ORM-generated ir.model / field / selection xmlids.

    Join ir_model_fields on (model, name). Never LIKE: `_` is a wildcard.
    """
    old_key = old_model.replace('.', '_')
    new_key = new_model.replace('.', '_')
    old_model_xmlid = 'model_%s' % old_key
    new_model_xmlid = 'model_%s' % new_key
    cr.execute(
        """
        SELECT 1 FROM ir_model_data
         WHERE name = %s AND model = 'ir.model'
        """,
        (new_model_xmlid,),
    )
    if cr.fetchone():
        raise AssertionError(
            'xmlid %s already exists while renaming %s' % (new_model_xmlid, old_model)
        )
    cr.execute(
        """
        UPDATE ir_model_data
           SET name = %s
         WHERE model = 'ir.model' AND name = %s
        """,
        (new_model_xmlid, old_model_xmlid),
    )
    field_prefix = 'field_%s__' % new_key
    cr.execute(
        """
        UPDATE ir_model_data d
           SET name = %s || f.name
          FROM ir_model_fields f
         WHERE d.model = 'ir.model.fields'
           AND d.res_id = f.id
           AND f.model = %s
        """,
        (field_prefix, new_model),
    )
    _logger.info('renamed %s field xmlids for %s', cr.rowcount, new_model)
    # Odoo selection_xmlid: value.replace('.', '_').replace(' ', '_').lower()
    # Raw s.value 'dtmf speech' violates ir_model_data_name_nospaces
    # (harrison-staging-38404402).
    selection_prefix = 'selection__%s__' % new_key
    cr.execute(
        """
        UPDATE ir_model_data d
           SET name = %s || f.name || '__'
                      || replace(replace(lower(s.value), '.', '_'), ' ', '_')
          FROM ir_model_fields_selection s
          JOIN ir_model_fields f ON f.id = s.field_id
         WHERE d.model = 'ir.model.fields.selection'
           AND d.res_id = s.id
           AND f.model = %s
        """,
        (selection_prefix, new_model),
    )
    _logger.info('renamed %s selection xmlids for %s', cr.rowcount, new_model)


def _reown_and_rename_ir(cr):
    """Re-own ir.model/fields/xmlids connect→connect_twilio; UPDATE ir_model.model
    to connect.twilio.*; remap exten.model Char callflow|twiml. Exact allowlists.
    Same-name movers: whatsapp_sender, message_content_template, whatsapp_composer.
    """
    old_models = tuple(old for old, _new in MOVE_MODELS)
    new_models = tuple(new for _old, new in MOVE_MODELS)

    # 1. Rename model identities before re-owning so xmlids follow the new name.
    for old, new in MOVE_MODELS:
        cr.execute("UPDATE ir_model SET model = %s WHERE model = %s", (new, old))
        cr.execute(
            "UPDATE ir_model_fields SET model = %s WHERE model = %s",
            (new, old),
        )
        cr.execute(
            "UPDATE ir_model_fields SET relation = %s WHERE relation = %s",
            (new, old),
        )
        if _table_exists(cr, 'ir_ui_view'):
            cr.execute("UPDATE ir_ui_view SET model = %s WHERE model = %s", (new, old))
        if _table_exists(cr, 'ir_act_window'):
            cr.execute(
                "UPDATE ir_act_window SET res_model = %s WHERE res_model = %s",
                (new, old),
            )
        _rename_auto_xmlids(cr, old, new)
        cr.execute(
            "UPDATE ir_model_data SET model = %s WHERE model = %s",
            (new, old),
        )
        if cr.rowcount:
            _logger.info(
                'retargeted %s ir_model_data rows %s -> %s',
                cr.rowcount, old, new,
            )
        _logger.info('renamed model identity %s -> %s', old, new)

    # 2. Re-own moved models (now under connect.twilio.*) to connect_twilio.
    _reown_models(cr, new_models, NEW_MODULE)
    _reown_fields_by_model(cr, new_models, NEW_MODULE)

    # 3. Same-name movers keep `_name`, change module.
    _reown_models(cr, SAME_NAME_MODELS, NEW_MODULE)
    _reown_fields_by_model(cr, SAME_NAME_MODELS, NEW_MODULE)

    # 4. KEEP-model fields that connect_twilio now declares.
    _reown_fields_named(cr, TWILIO_KEEP_FIELDS, NEW_MODULE)

    # 5. Fields connect_pbx declares on the renamed models (already pbx-owned
    #    from 1.32.0 stay pbx; any still on connect move to pbx).
    _reown_fields_named(cr, PBX_MOVE_FIELDS, PBX_MODULE)

    # 6. Named xmlids that moved with the Twilio surface.
    cr.execute(
        """
        UPDATE ir_model_data
           SET module = %s
         WHERE module = %s
           AND name IN %s
        """,
        (NEW_MODULE, OLD_MODULE, MOVED_XMLID_NAMES),
    )
    _logger.info('re-owned %s named xmlids to %s', cr.rowcount, NEW_MODULE)

    # 7. Group / menu / view xmlid renames so NG XML updates in place.
    for old, new in RENAME_XMLIDS:
        _rename_xmlid(cr, old, new)
        _alias_xmlid(cr, new, old)

    # 8. Exten.model Char values stored as connect.callflow / connect.twiml.
    if _table_exists(cr, 'connect_twilio_exten'):
        for old, new in EXTEN_MODEL_REMAP:
            cr.execute(
                """
                UPDATE connect_twilio_exten
                   SET model = %s
                 WHERE model = %s
                """,
                (new, old),
            )
            if cr.rowcount:
                _logger.info(
                    'remapped %s exten.model %s -> %s', cr.rowcount, old, new
                )

    # 9. Constraints / m2m bookkeeping follow the new models.
    cr.execute("SELECT id FROM ir_module_module WHERE name = %s", (NEW_MODULE,))
    row = cr.fetchone()
    if row:
        module_id = row[0]
        cr.execute(
            """
            UPDATE ir_model_constraint c
               SET module = %s
              FROM ir_model m
             WHERE c.model = m.id
               AND m.model IN %s
            """,
            (module_id, new_models + SAME_NAME_MODELS),
        )
        _logger.info('re-owned %s constraints to %s', cr.rowcount, NEW_MODULE)
        cr.execute(
            """
            UPDATE ir_model_relation
               SET module = %s
             WHERE name = %s
            """,
            (module_id, 'connect_twilio_callflow_connect_user_rel'),
        )


def _rename_user_columns(cr):
    if not _table_exists(cr, 'connect_user'):
        return
    for old, new in USER_COLUMN_RENAMES:
        if _column_exists(cr, 'connect_user', old) and not _column_exists(
            cr, 'connect_user', new
        ):
            cr.execute(
                'ALTER TABLE connect_user RENAME COLUMN "%s" TO "%s"' % (old, new)
            )
            _logger.info('renamed connect_user.%s -> %s', old, new)
        cr.execute(
            """
            UPDATE ir_model_fields
               SET name = %s
             WHERE model = 'connect.user' AND name = %s
            """,
            (new, old),
        )


def migrate(cr, version):
    if not version:
        return

    snapshot_tables = (
        COUNT_KPI + KEEP_TABLES + tuple(s for s, _d in MOVE_TABLES) + PBX_TABLES
    )
    _snapshot(cr, snapshot_tables)
    for src, dst in MOVE_TABLES:
        _rename_table(cr, src, dst)
    for item in MOVE_REL:
        _rename_rel(cr, *item)
    _rename_user_columns(cr)
    _reown_and_rename_ir(cr)
    _logger.info('connect 19.0.4.4.0 pre-migration complete')
