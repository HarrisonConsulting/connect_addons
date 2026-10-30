from odoo import fields, models


class ConnectTool(models.Model):
    _inherit = 'connect.tool'

    adapter = fields.Selection(
        selection_add=[('textable', 'Textable')],
        ondelete={'textable': 'set null'},
    )
