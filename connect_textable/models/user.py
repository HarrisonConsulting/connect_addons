from odoo import fields, models


class ConnectUser(models.Model):
    _inherit = 'connect.user'

    message_provider = fields.Selection(
        selection_add=[('textable', 'Textable')],
        ondelete={'textable': 'set null'},
    )
    textable_number_ids = fields.Many2many(
        'connect.textable.number', 'connect_textable_number_user_rel',
        'user_id', 'number_id', string='Textable Numbers', readonly=True,
        help='Textable numbers this user may send from. Assign them on the '
             'number (Connect > Configuration > Textable > Numbers).')
