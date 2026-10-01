# -*- coding: utf-8 -*-

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class Endpoint(models.Model):
    _name = 'connect.endpoint'
    _description = 'Desk Phone'
    _order = 'name, id'

    name = fields.Char(
        required=True,
        help='Name of this desk phone.',
    )
    vendor = fields.Selection(
        selection=[],
        string='Vendor',
        help="The adapter that knows this phone's slot count and line count.",
    )
    product_id = fields.Many2one(
        'product.product',
        string='Product',
        help='The catalog product this phone is.',
    )
    account_ids = fields.One2many(
        'connect.endpoint.account',
        'endpoint_id',
        string='Accounts',
        help='SIP registrations on this phone.',
    )
    line_ids = fields.One2many(
        'connect.endpoint.line',
        'endpoint_id',
        string='Line keys',
        help='Line keys that present an account. An account can be registered without a line key.',
    )

    def account_ceiling(self):
        """Maximum SIP accounts. 0 means the adapter has not declared a ceiling."""
        return 0

    def line_ceiling(self):
        """Maximum line keys. 0 means the adapter has not declared a ceiling."""
        return 0

    @api.constrains('account_ids')
    def _check_account_ceiling(self):
        for rec in self:
            ceiling = rec.account_ceiling()
            if not ceiling or ceiling <= 0:
                continue
            if len(rec.account_ids) > ceiling:
                raise ValidationError(
                    "This desk phone cannot hold more than %s SIP accounts." % ceiling
                )

    @api.constrains('line_ids')
    def _check_line_ceiling(self):
        for rec in self:
            ceiling = rec.line_ceiling()
            if not ceiling or ceiling <= 0:
                continue
            if len(rec.line_ids) > ceiling:
                raise ValidationError(
                    "This desk phone cannot hold more than %s line keys." % ceiling
                )
