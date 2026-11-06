from odoo import models, fields


class Favorite(models.Model):
    _name = 'connect.favorite'
    _order = 'id desc'
    _description = 'Favorite'

    name = fields.Char()
    phone_number = fields.Char(required=True)
    user = fields.Many2one('res.users', ondelete='set null')
    partner = fields.Many2one('res.partner', ondelete='set null')
    line_key = fields.Char(
        readonly=True,
        help='The phone line of the call this favorite was starred from. Calling '
             'the favorite uses that line once. Empty means the line selected in '
             'the phone.')
