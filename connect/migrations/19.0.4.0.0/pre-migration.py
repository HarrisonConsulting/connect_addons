"""No-op replacement for NG 19.0.4.0.0.

NG's script archives connect_exten / connect_callflow / connect_number / …
to `_*_legacy` and DROP TABLE source CASCADE if the archive already exists.
That path loses Twilio rows. We only assert zero `_legacy` tables and never
DROP or archive.
"""

import logging

_logger = logging.getLogger(__name__)

# Exact names NG 19.0.4.0.0 would have created. Never LIKE: `_` is a wildcard.
NG_LEGACY_TABLES = (
    '_connect_exten_legacy',
    '_connect_callflow_legacy',
    '_connect_callflow_choice_legacy',
    '_connect_callflow_connect_user_rel_legacy',
    '_connect_number_legacy',
    '_connect_endpoint_legacy',
    '_connect_outgoing_callerid_legacy',
    '_connect_user_callflow_legacy',
    '_connect_user_callflow_call_legacy',
    '_connect_message_configuration_legacy',
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

    legacy = [t for t in NG_LEGACY_TABLES if _table_exists(cr, t)]
    if legacy:
        raise AssertionError(
            'connect 19.0.4.0.0 no-op: _legacy tables present, NG archive '
            'must not have run: %s' % (legacy,)
        )

    _logger.info('connect 19.0.4.0.0 no-op: zero _legacy tables')
