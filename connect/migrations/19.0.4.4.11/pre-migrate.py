"""Recreate the legacy group xmlids before views are loaded.

Views still say groups="connect.group_connect_user". The canonical record
is group_user. Creating the old name in a post-migrate is too late: the
view loader has already warned that the group does not exist.
"""

GROUP_ALIASES = (
    ('group_admin', 'group_connect_admin'),
    ('group_user', 'group_connect_user'),
    ('group_webhook', 'group_connect_webhook'),
)


def migrate(cr, version):
    for canonical, alias in GROUP_ALIASES:
        cr.execute(
            """
            SELECT model, res_id
              FROM ir_model_data
             WHERE module = 'connect' AND name = %s
            """,
            (canonical,),
        )
        row = cr.fetchone()
        if not row:
            continue
        cr.execute(
            """
            INSERT INTO ir_model_data
                (module, name, model, res_id, noupdate,
                 create_uid, create_date, write_uid, write_date)
            SELECT 'connect', %s, %s, %s, true, 1, NOW(), 1, NOW()
             WHERE NOT EXISTS (
                   SELECT 1 FROM ir_model_data
                    WHERE module = 'connect' AND name = %s
             )
            """,
            (alias, row[0], row[1], alias),
        )
