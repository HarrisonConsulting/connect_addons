"""Keep the pre-rename recording view xmlids resolvable.

The recording form and list were renamed to view_connect_recording_*.
Call and user views already get a legacy alias. Recordings did not, so an
inherit of connect.connect_recording_form fails on a database that only has
the new xmlid.
"""


def migrate(cr, version):
    aliases = (
        ('view_connect_recording_form', 'connect_recording_form'),
        ('view_connect_recording_tree', 'connect_recording_list'),
    )
    for canonical, alias in aliases:
        cr.execute(
            """
            SELECT model, res_id, noupdate
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
            SELECT 'connect', %s, %s, %s, %s, 1, NOW(), 1, NOW()
             WHERE NOT EXISTS (
                   SELECT 1 FROM ir_model_data
                    WHERE module = 'connect' AND name = %s
             )
            """,
            (alias, row[0], row[1], row[2], alias),
        )
