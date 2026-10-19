"""Keep the VoiceTel columns that the retirement release would drop.

19.0.3.0.0 declares no models. At the end of the upgrade Odoo removes
the fields this module added to connect.user and connect.settings, and
that drops their columns. Copy them first into archive tables. The
connect_voicetel_* model tables are not touched: Odoo leaves the table of
a model that is no longer in the registry, so they stay as they are.

Exact column allowlists, guarded by existence. Never DROP.
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
