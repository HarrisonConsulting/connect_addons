from odoo import fields, models


class Tool(models.Model):
    _inherit = 'connect.tool'

    adapter = fields.Selection(
        selection_add=[('twilio', 'Twilio')],
        ondelete={'twilio': 'set null'},
    )
