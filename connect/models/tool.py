from odoo import fields, models


class Tool(models.Model):
    _name = 'connect.tool'
    _description = 'Voice system'
    _order = 'name'

    name = fields.Char(
        required=True,
        help='A person recognizes this system by this name.',
    )
    adapter = fields.Selection(
        [('sip', 'SIP registrar')],
        help="Which module owns this system's credentials and dial policy.",
    )
    company_id = fields.Many2one(
        'res.company',
        default=lambda self: self.env.company,
        help='Company this system belongs to.',
    )
    sip_server = fields.Char(
        help='Registrar a phone registers to for this system.',
    )
    active = fields.Boolean(
        default=True,
        help='Uncheck to retire this system without deleting it.',
    )
    assignment_ids = fields.One2many(
        'connect.user.tool', 'tool_id',
        help='People assigned to this system.',
    )
