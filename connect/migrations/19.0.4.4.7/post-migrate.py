"""Recreate the pre-rename Connect xmlids and keep them.

A finished connect update drops any ir.model.data row that the module's
files did not load and that is not noupdate. Groups and menus are still referenced under their previous names.
View xmlids are not aliased: a second ir.model.data row makes every
upgrade look for that name in the view file and warn.
"""

ALIASES = (
    ('group_admin', 'group_connect_admin'),
    ('group_user', 'group_connect_user'),
    ('group_webhook', 'group_connect_webhook'),
    ('module_category_connect', 'module_connect_category'),
    ('menu_connect_root', 'connect_top_menu'),
    ('menu_connect_settings', 'connect_settings_menu'),
)


def migrate(cr, version):
    for canonical, alias in ALIASES:
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
        cr.execute(
            """
            UPDATE ir_model_data
               SET noupdate = true, res_id = %s, model = %s
             WHERE module = 'connect' AND name = %s
            """,
            (row[1], row[0], alias),
        )
