# -*- coding: utf-8 -*-
"""Copy VoiceTel account columns into ir.config_parameter.

A key is written only when it is absent and the column is non-empty.
Repeating the upgrade does not overwrite a key that is already there.
"""

_COPIES = (
    ('voicetel_account_sid', 'connect_voicetel.account_sid'),
    ('voicetel_api_key', 'connect_voicetel.api_key'),
    ('voicetel_api_secret', 'connect_voicetel.api_secret'),
    ('voicetel_rest_host', 'connect_voicetel.rest_host'),
)


def migrate(cr, version):
    cr.execute(
        """
        SELECT voicetel_account_sid, voicetel_api_key,
               voicetel_api_secret, voicetel_rest_host
          FROM connect_settings
         ORDER BY id
         LIMIT 1
        """
    )
    row = cr.fetchone()
    if not row:
        return
    for (_column, key), value in zip(_COPIES, row):
        if value is None or not str(value).strip():
            continue
        cr.execute(
            """
            INSERT INTO ir_config_parameter (key, value, create_date, write_date)
            SELECT %s, %s, NOW(), NOW()
             WHERE NOT EXISTS (
                   SELECT 1 FROM ir_config_parameter WHERE key = %s
             )
            """,
            (key, value, key),
        )
