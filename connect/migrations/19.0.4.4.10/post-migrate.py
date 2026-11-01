"""Drop legacy view xmlids again after older post-migrates.

A database that has not yet run 19.0.4.4.7 recreates those rows in that
version's post-migrate, which runs before this one. Delete them after.
"""

_VIEW_ALIAS_NAMES = (
    'connect_call_form',
    'connect_call_list',
    'connect_user_form',
    'user_list',
    'view_connect_sms_message_form',
    'view_connect_sms_message_tree',
    'connect_recording_form',
    'connect_recording_list',
)


def migrate(cr, version):
    cr.execute(
        """
        DELETE FROM ir_model_data
         WHERE module = 'connect'
           AND model = 'ir.ui.view'
           AND name = ANY(%s)
        """,
        (list(_VIEW_ALIAS_NAMES),),
    )
