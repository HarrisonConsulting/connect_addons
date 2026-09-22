"""Drop legacy view xmlids before the view loader runs.

The NG view records live under view_connect_* names. A second ir.model.data
row under the old name makes Odoo look for that name in the view file on
every upgrade and warn when it is not there. The view row itself stays.
Group xmlids are left in place: callers still reference them.
"""

VIEW_ALIAS_NAMES = (
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
        (list(VIEW_ALIAS_NAMES),),
    )
