# -*- coding: utf-8 -*-

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class EndpointAccount(models.Model):
    _name = 'connect.endpoint.account'
    _description = 'Desk Phone SIP Account'
    _order = 'slot, id'

    endpoint_id = fields.Many2one(
        'connect.endpoint',
        required=True,
        ondelete='cascade',
        help='Desk phone that holds this registration.',
    )
    slot = fields.Integer(
        required=True,
        help='The account index the phone uses.',
    )
    name = fields.Char(
        help='Label shown for this registration.',
    )
    sip_user = fields.Char(
        string='SIP user',
        help='SIP user id sent in the REGISTER.',
    )
    tool_id = fields.Many2one(
        'connect.tool',
        required=True,
        ondelete='restrict',
        help='The system this registration belongs to.',
    )
    sip_server = fields.Char(
        related='tool_id.sip_server',
        string='SIP server',
        help='SIP server of the system this registration belongs to.',
    )
    user_id = fields.Many2one(
        'connect.user',
        ondelete='set null',
        help='The person this line is for. Empty when the phone holds the line without a person yet.',
    )
    transport = fields.Selection(
        [
            ('udp', 'UDP'),
            ('tcp', 'TCP'),
            ('tls', 'TLS'),
        ],
        default='udp',
        help='Transport used for the SIP REGISTER.',
    )
    registration = fields.Selection(
        [
            ('unregistered', 'Unregistered'),
            ('registered', 'Registered'),
            ('failed', 'Failed'),
        ],
        default='unregistered',
        readonly=True,
        help='Registration state. Only the mark methods change it.',
    )

    _slot_unique = models.Constraint(
        'UNIQUE(endpoint_id, slot)',
        'This phone already has an account in that slot.',
    )

    @api.constrains('endpoint_id')
    def _check_endpoint_account_ceiling(self):
        self.endpoint_id._check_account_ceiling()

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get('connect_endpoint_mark_registration'):
            for vals in vals_list:
                if vals.get('registration') not in (None, False, 'unregistered'):
                    raise ValidationError(
                        "Set registration with mark_registered, mark_unregistered, or mark_failed."
                    )
        return super().create(vals_list)

    def write(self, vals):
        if (
            'registration' in vals
            and not self.env.context.get('connect_endpoint_mark_registration')
        ):
            raise ValidationError(
                "Set registration with mark_registered, mark_unregistered, or mark_failed."
            )
        return super().write(vals)

    def mark_registered(self):
        self._set_registration('registered')

    def mark_unregistered(self):
        self._set_registration('unregistered')

    def mark_failed(self):
        self._set_registration('failed')

    def _set_registration(self, state):
        self.with_context(connect_endpoint_mark_registration=True).write({
            'registration': state,
        })
