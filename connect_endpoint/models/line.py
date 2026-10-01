# -*- coding: utf-8 -*-

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class EndpointLine(models.Model):
    _name = 'connect.endpoint.line'
    _description = 'Desk Phone Line Key'
    _order = 'sequence, id'

    endpoint_id = fields.Many2one(
        'connect.endpoint',
        required=True,
        ondelete='cascade',
        help='Desk phone that shows this line key.',
    )
    account_id = fields.Many2one(
        'connect.endpoint.account',
        required=True,
        ondelete='cascade',
        help='SIP account this line key presents.',
    )
    sequence = fields.Integer(
        default=10,
        help='Order of this line key on the phone.',
    )

    @api.constrains('endpoint_id', 'account_id')
    def _check_account_endpoint(self):
        for rec in self:
            if rec.account_id.endpoint_id != rec.endpoint_id:
                raise ValidationError(
                    "A line key can only present an account on the same desk phone."
                )

    @api.constrains('endpoint_id')
    def _check_line_key_ceiling(self):
        self.endpoint_id._check_line_ceiling()
