"""Keep the VoiceTel columns that the retirement release would drop.

19.0.3.0.0 declares no models. At the end of the upgrade Odoo removes
the fields this module added to connect.user and connect.settings, and
that drops their columns. Copy them first into archive tables. The
connect_voicetel_* model tables are not touched: Odoo leaves the table of
a model that is no longer in the registry, so they stay as they are.

Exact column allowlists, guarded by existence. Never DROP.

The old module's views, actions and menus stay in the database until the
end-of-update cleanup. Dependents such as connect_pbx re-validate their
parent views first and fail on a stale child that names a removed field.
So the records the old module owned are deleted here, after the archive.
"""

import logging

_logger = logging.getLogger(__name__)

ARCHIVES = {
    'connect_settings': (
        'connect_voicetel_settings_archive',
        (
            'voicetel_account_sid',
            'voicetel_api_key',
            'display_voicetel_api_key',
            'voicetel_rest_host',
            'voicetel_wss_url',
            'voicetel_softphone',
            'voicetel_verify_requests',
            'voicetel_auto_sync',
            'rest_provider',
        ),
    ),
    'connect_user': (
        'connect_voicetel_user_archive',
        (
            'voicetel_username',
            'voicetel_sid',
            'voicetel_password',
            'voicetel_domain',
            'voicetel_sip_enabled',
            'voicetel_client_enabled',
            'voicetel_sip_ring_timeout',
            'voicetel_client_ring_timeout',
            'voicetel_application',
            'voicetel_exten',
            'voicetel_exten_number',
            'voicetel_outgoing_callerid',
            'originate_provider',
            'message_provider',
        ),
    ),
}

# Tables of the records the old module owned that name removed fields or
# models. Children of a view go with it through inherit_id ON DELETE CASCADE.
# Menus first: they point at the actions. Models, fields and the
# connect_voicetel_* data rows are left to Odoo's own cleanup.
OWNED_RECORDS = (
    ('ir.ui.menu', 'ir_ui_menu'),
    ('ir.ui.view', 'ir_ui_view'),
    ('ir.actions.act_window', 'ir_act_window'),
    ('ir.actions.server', 'ir_act_server'),
    ('ir.cron', 'ir_cron'),
    ('ir.rule', 'ir_rule'),
    ('ir.model.access', 'ir_model_access'),
    ('ir.filters', 'ir_filters'),
)


def _existing_columns(cr, table, columns):
    cr.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = current_schema()"
        " AND table_name = %s AND column_name IN %s",
        (table, tuple(columns)),
    )
    found = {row[0] for row in cr.fetchall()}
    return [c for c in columns if c in found]


def _table_exists(cr, table):
    cr.execute(
        "SELECT 1 FROM information_schema.tables"
        " WHERE table_schema = current_schema() AND table_name = %s",
        (table,),
    )
    return bool(cr.fetchone())


def migrate(cr, version):
    if not version:
        return
    for source, (archive, columns) in ARCHIVES.items():
        if _table_exists(cr, archive):
            _logger.info('connect_voicetel: %s already exists, skipped', archive)
            continue
        present = _existing_columns(cr, source, columns)
        if not any(c.startswith(('voicetel_', 'display_voicetel_')) for c in present):
            _logger.info('connect_voicetel: no VoiceTel columns on %s', source)
            continue
        select = ', '.join(['id'] + ['"{}"'.format(c) for c in present])
        cr.execute('CREATE TABLE "{}" AS SELECT {} FROM "{}"'.format(
            archive, select, source))
        _logger.info(
            'connect_voicetel: copied %s rows, columns %s, from %s to %s',
            cr.rowcount, ', '.join(present), source, archive)

    cr.execute(
        "SELECT table_name FROM information_schema.tables"
        " WHERE table_schema = current_schema()"
        " AND table_name LIKE 'connect\\_voicetel\\_%%'"
        " ORDER BY table_name")
    for (table,) in cr.fetchall():
        cr.execute('SELECT count(*) FROM "{}"'.format(table))
        _logger.info('connect_voicetel: %s keeps %s rows', table, cr.fetchone()[0])


    _drop_owned_records(cr)


def _drop_owned_records(cr):
    for model, table in OWNED_RECORDS:
        if not _table_exists(cr, table):
            continue
        cr.execute(
            "SELECT res_id FROM ir_model_data"
            " WHERE module = 'connect_voicetel' AND model = %s"
            " AND res_id IS NOT NULL", (model,))
        ids = [row[0] for row in cr.fetchall()]
        if ids:
            if model == 'ir.ui.view':
                # delete the whole inherit tree under the owned views
                cr.execute(
                    "WITH RECURSIVE tree AS ("
                    " SELECT id FROM ir_ui_view WHERE id IN %s"
                    " UNION SELECT v.id FROM ir_ui_view v"
                    " JOIN tree t ON v.inherit_id = t.id)"
                    " SELECT id FROM tree", (tuple(ids),))
                ids = [row[0] for row in cr.fetchall()]
            if model in ('ir.actions.act_window', 'ir.actions.server'):
                table = 'ir_actions'
            cr.execute('DELETE FROM "{}" WHERE id IN %s'.format(table), (tuple(ids),))
            _logger.info(
                'connect_voicetel: deleted %s %s records of the retired module',
                cr.rowcount, model)
        else:
            _logger.info('connect_voicetel: no %s records to delete', model)
        cr.execute(
            "DELETE FROM ir_model_data"
            " WHERE module = 'connect_voicetel' AND model = %s", (model,))
