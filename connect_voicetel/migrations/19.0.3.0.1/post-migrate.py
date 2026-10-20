"""Clear selections that still name the retired 'voicetel' provider.

The module no longer adds 'voicetel' to any selection, so a row that still
holds it points at nothing. Set those cells to NULL, retire connect.tool
rows whose adapter was 'voicetel', and drop a rest_provider config
parameter that still says 'voicetel'. Raw SQL, guarded by column
existence, idempotent: a second run matches no rows.
"""

import logging

_logger = logging.getLogger(__name__)

# (table, columns) holding a provider selection. connect_user owns
# originate/message_provider; connect_settings is listed too in case a
# database still carries them there.
PROVIDER_COLUMNS = (
    ('connect_settings', ('originate_provider', 'message_provider', 'rest_provider')),
    ('connect_user', ('originate_provider', 'message_provider')),
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


def migrate(cr, version):
    if not version:
        return
    for table, columns in PROVIDER_COLUMNS:
        for column in _existing_columns(cr, table, columns):
            cr.execute(
                'UPDATE "{}" SET "{}" = NULL WHERE "{}" = %s'.format(
                    table, column, column),
                ('voicetel',))
            _logger.info(
                'connect_voicetel: cleared %s %s.%s rows',
                cr.rowcount, table, column)

    if len(_existing_columns(cr, 'connect_tool', ('adapter', 'active'))) == 2:
        cr.execute(
            "UPDATE connect_tool SET adapter = NULL, active = FALSE"
            " WHERE adapter = %s",
            ('voicetel',))
        _logger.info(
            'connect_voicetel: retired %s connect_tool rows', cr.rowcount)

    cr.execute(
        "DELETE FROM ir_config_parameter WHERE key = %s AND value = %s",
        ('rest_provider', 'voicetel'))
    _logger.info(
        'connect_voicetel: removed %s rest_provider config parameters',
        cr.rowcount)
