# -*- coding: utf-8 -*-
"""connect_voicetel 19.0.2.0.0 — standalone provider.

Three forward-only, idempotent moves, each guarded so a re-run is a no-op:

1. The old settings screen stored its account under
   ``ir.config_parameter`` keys (``connect_voicetel.account_sid`` etc, the
   Odoo Settings-app convention for a ``res.config.settings`` field). Every
   credential now lives only on the ``connect.settings`` columns of the
   same name; copy a param into its column when the column is empty, then
   drop the params — nothing reads them anymore.
2. ``voicetel_api_secret`` / ``display_voicetel_api_secret`` had no use (one
   VoiceTel API key serves both REST auth and webhook signatures); drop the
   orphaned columns.
3. Production holds 0 rows in the per-browser-tab SIP credential table, so
   this is a pure identity rename, not a data migration: table,
   ``ir_model``, ``ir_model_fields`` and their ``ir_model_data`` xmlids move
   from ``connect.user_browser_credential`` to
   ``connect.voicetel.browser_credential`` in lockstep.
"""

_ICP_COPIES = (
    ('voicetel_account_sid', 'connect_voicetel.account_sid'),
    ('voicetel_api_key', 'connect_voicetel.api_key'),
    ('voicetel_rest_host', 'connect_voicetel.rest_host'),
)
_ICP_DROP_ONLY = (
    'connect_voicetel.api_secret',
)

_OLD_MODEL = 'connect.user_browser_credential'
_NEW_MODEL = 'connect.voicetel.browser_credential'
_OLD_TABLE = 'connect_user_browser_credential'
_NEW_TABLE = 'connect_voicetel_browser_credential'


def _column_exists(cr, table, column):
    cr.execute(
        "SELECT 1 FROM information_schema.columns"
        " WHERE table_name = %s AND column_name = %s",
        (table, column))
    return bool(cr.fetchone())


def _table_exists(cr, table):
    cr.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = %s", (table,))
    return bool(cr.fetchone())


def _migrate_icp_credentials(cr):
    for column, key in _ICP_COPIES:
        if not _column_exists(cr, 'connect_settings', column):
            continue
        cr.execute(
            "SELECT value FROM ir_config_parameter WHERE key = %s", (key,))
        row = cr.fetchone()
        if not row or not (row[0] or '').strip():
            continue
        cr.execute(
            "UPDATE connect_settings SET {} = %s"
            " WHERE ({} IS NULL OR {} = '')".format(column, column, column),
            (row[0],))
    for key in [k for _c, k in _ICP_COPIES] + list(_ICP_DROP_ONLY):
        cr.execute("DELETE FROM ir_config_parameter WHERE key = %s", (key,))


def _drop_api_secret_columns(cr):
    for column in ('voicetel_api_secret', 'display_voicetel_api_secret'):
        if _column_exists(cr, 'connect_settings', column):
            cr.execute(
                'ALTER TABLE connect_settings DROP COLUMN "{}"'.format(column))


def _rename_browser_credential_model(cr):
    if not _table_exists(cr, _OLD_TABLE):
        return  # already renamed, or never installed (fresh install)
    if _table_exists(cr, _NEW_TABLE):
        return  # a previous partial run already got this far
    cr.execute('ALTER TABLE "{}" RENAME TO "{}"'.format(_OLD_TABLE, _NEW_TABLE))
    cr.execute(
        "SELECT 1 FROM information_schema.sequences WHERE sequence_name = %s",
        (_OLD_TABLE + '_id_seq',))
    if cr.fetchone():
        cr.execute('ALTER SEQUENCE "{}_id_seq" RENAME TO "{}_id_seq"'.format(
            _OLD_TABLE, _NEW_TABLE))
    cr.execute(
        "UPDATE ir_model SET model = %s WHERE model = %s",
        (_NEW_MODEL, _OLD_MODEL))
    cr.execute(
        "UPDATE ir_model_fields SET model = %s WHERE model = %s",
        (_NEW_MODEL, _OLD_MODEL))
    old_model_xmlid = 'model_' + _OLD_MODEL.replace('.', '_')
    new_model_xmlid = 'model_' + _NEW_MODEL.replace('.', '_')
    cr.execute(
        "UPDATE ir_model_data SET name = %s"
        " WHERE name = %s AND model = 'ir.model' AND module = 'connect_voicetel'",
        (new_model_xmlid, old_model_xmlid))
    cr.execute(
        "UPDATE ir_model_data SET name = replace(name, %s, %s)"
        " WHERE model = 'ir.model.fields' AND module = 'connect_voicetel'"
        " AND name LIKE %s",
        ('field_' + _OLD_MODEL.replace('.', '_') + '__',
         'field_' + _NEW_MODEL.replace('.', '_') + '__',
         'field_' + _OLD_MODEL.replace('.', '_') + '__%'))


def migrate(cr, version):
    _migrate_icp_credentials(cr)
    _drop_api_secret_columns(cr)
    _rename_browser_credential_model(cr)
