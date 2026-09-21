"""No-op replacement for NG 19.0.3.1.0.

NG's script archives live PBX tables (connect_scheduled_call,
connect_documentation) and DROP TABLE if an archive already exists. That
must never run against this fork. We only assert the PBX tables are still
present and that no `_connect_*_archive` table appeared.
"""

import logging

_logger = logging.getLogger(__name__)

PBX_TABLES = (
    'connect_documentation',
    'connect_scheduled_call',
)

# Exact names NG 19.0.3.1.0 would have created. Never LIKE: `_` is a wildcard.
NG_ARCHIVE_TABLES = (
    '_connect_pbx_group_archive',
    '_connect_scheduled_call_archive',
    '_connect_transcription_rule_archive',
    '_connect_documentation_archive',
    '_connect_pbx_group_connect_user_rel_archive',
    '_connect_user_connect_pbx_group_rel_archive',
    '_connect_pbx_group_res_users_rel_archive',
)


def _table_exists(cr, table):
    cr.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
        (table,),
    )
    return bool(cr.fetchone())


def migrate(cr, version):
    if not version:
        return

    missing = [t for t in PBX_TABLES if not _table_exists(cr, t)]
    if missing:
        raise AssertionError(
            'connect 19.0.3.1.0 no-op: PBX tables missing, refusing to '
            'continue: %s' % (missing,)
        )

    archives = [t for t in NG_ARCHIVE_TABLES if _table_exists(cr, t)]
    if archives:
        raise AssertionError(
            'connect 19.0.3.1.0 no-op: archive tables present, NG archive '
            'script must not have run: %s' % (archives,)
        )

    _logger.info(
        'connect 19.0.3.1.0 no-op: PBX tables %s present, no _archive tables',
        PBX_TABLES,
    )
