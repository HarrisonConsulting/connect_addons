"""Copy Twilio account columns into ir.config_parameter.

Readers prefer connect_twilio.* and fall back to the column. A parameter
that already exists is left alone. Booleans are stored as 'True' or 'False'.
"""

import re

_PAIRS = (
    ('account_sid', 'connect_twilio.account_sid', 'text'),
    ('auth_token', 'connect_twilio.auth_token', 'text'),
    ('twilio_api_key', 'connect_twilio.api_key', 'text'),
    ('twilio_api_secret', 'connect_twilio.api_secret', 'text'),
    ('twilio_region', 'connect_twilio.region', 'text'),
    ('twilio_edge', 'connect_twilio.edge', 'text'),
    ('twilio_verify_requests', 'connect_twilio.verify_requests', 'bool'),
    ('twilio_auto_sync', 'connect_twilio.auto_sync', 'bool'),
    ('fetch_call_prices', 'connect_twilio.fetch_call_prices', 'bool'),
)

_COLUMN_NAME = re.compile(r'^[a-z_]+$')


def _column_exists(cr, table, column):
    cr.execute(
        """
        SELECT 1
          FROM information_schema.columns
         WHERE table_name = %s
           AND column_name = %s
        """,
        (table, column),
    )
    return bool(cr.fetchone())


def _stored_value(kind, value):
    if kind == 'bool':
        if value is None:
            return None
        return 'True' if value else 'False'
    if value is None or value == '':
        return None
    return str(value)


def migrate(cr, version):
    if not version:
        return
    for column, key, kind in _PAIRS:
        if not _COLUMN_NAME.match(column):
            continue
        if not _column_exists(cr, 'connect_settings', column):
            continue
        cr.execute(
            "SELECT 1 FROM ir_config_parameter WHERE key = %s",
            (key,),
        )
        if cr.fetchone():
            continue
        cr.execute(
            'SELECT "%s" FROM connect_settings ORDER BY id LIMIT 1' % column,
        )
        row = cr.fetchone()
        if not row:
            continue
        stored = _stored_value(kind, row[0])
        if stored is None:
            continue
        cr.execute(
            """
            INSERT INTO ir_config_parameter
                (key, value, create_uid, create_date, write_uid, write_date)
            SELECT %s, %s, 1, NOW(), 1, NOW()
             WHERE NOT EXISTS (
                   SELECT 1 FROM ir_config_parameter WHERE key = %s
             )
            """,
            (key, stored, key),
        )
