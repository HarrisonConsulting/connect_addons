"""Recreate the legacy group xmlids before views are loaded.

Views and security data now reference group_user, group_admin, and
group_webhook. Those names are already in the registry when a remint
starts. The old names are still created here so a database that has not
been rewritten keeps resolving them.
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
