"""Prevent the upstream PBX archive migration from losing local records."""

import logging


_logger = logging.getLogger(__name__)

PBX_MODELS = (
    ('connect.documentation', 'connect_documentation'),
    ('connect.scheduled_call', 'connect_scheduled_call'),
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


def _model_exists(cr, model):
    cr.execute("SELECT 1 FROM ir_model WHERE model = %s", (model,))
    return bool(cr.fetchone())


def _declared_but_missing_tables(cr, model_tables=PBX_MODELS):
    return [
        (model, table)
        for model, table in model_tables
        if _model_exists(cr, model) and not _table_exists(cr, table)
    ]


def migrate(cr, version):
    if not version:
        return

    missing = _declared_but_missing_tables(cr)
    if missing:
        raise AssertionError(
            'connect 19.0.3.1.0 no-op: declared PBX model tables missing, '
            'refusing to continue: %s' % missing
        )

    archives = [t for t in NG_ARCHIVE_TABLES if _table_exists(cr, t)]
    if archives:
        raise AssertionError(
            'connect 19.0.3.1.0 no-op: archive tables present, NG archive '
            'script must not have run: %s' % (archives,)
        )

    _logger.info('connect 19.0.3.1.0 no-op: no declared PBX table is missing')
