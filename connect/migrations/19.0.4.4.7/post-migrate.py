"""Recreate the pre-rename Connect xmlids and keep them.

A finished connect update drops any ir.model.data row that the module's
files did not load and that is not noupdate. The renamed groups and views
are only known to older modules under their previous names, so those names
have to exist before the dependents load, and they have to be noupdate or
the same update deletes them again.
"""

ALIASES = (
    ('group_admin', 'group_connect_admin'),
    ('group_user', 'group_connect_user'),
    ('group_webhook', 'group_connect_webhook'),
    ('module_category_connect', 'module_connect_category'),
    ('menu_connect_root', 'connect_top_menu'),
    ('menu_connect_settings', 'connect_settings_menu'),
    ('view_connect_user_tree', 'user_list'),
    ('view_connect_user_form', 'connect_user_form'),
    ('view_connect_call_form', 'connect_call_form'),
    ('view_connect_call_tree', 'connect_call_list'),
    ('view_connect_message_form', 'view_connect_sms_message_form'),
    ('view_connect_message_tree', 'view_connect_sms_message_tree'),
    ('view_connect_recording_form', 'connect_recording_form'),
    ('view_connect_recording_tree', 'connect_recording_list'),
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
