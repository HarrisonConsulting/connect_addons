# -*- coding: utf-8 -*-
"""Twilio account on the Connect settings screen.

The values live in ir.config_parameter. connect.settings keeps the old
columns; readers try the parameter and fall back to the column.
"""
from odoo import fields, models

from .settings import TWILIO_EDGES

# Odoo drops a boolean parameter when the value is false. These keys must
# stay, as the string 'False', or a missing row would fall back to the column.
_BOOLEAN_PARAMS = (
    ('connect_twilio_verify_requests', 'connect_twilio.verify_requests'),
    ('connect_twilio_auto_sync', 'connect_twilio.auto_sync'),
    ('connect_twilio_fetch_call_prices', 'connect_twilio.fetch_call_prices'),
)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    connect_twilio_account_sid = fields.Char(
        string='Twilio Account SID',
        config_parameter='connect_twilio.account_sid',
    )
    connect_twilio_auth_token = fields.Char(
        string='Auth Token',
        config_parameter='connect_twilio.auth_token',
    )
    connect_twilio_api_key = fields.Char(
        string='API Key SID',
        config_parameter='connect_twilio.api_key',
    )
    connect_twilio_api_secret = fields.Char(
        string='API Key Secret',
        config_parameter='connect_twilio.api_secret',
    )
    connect_twilio_region = fields.Selection(
        selection=[
            ('us1', 'US East (Virginia)'),
            ('ie1', 'Ireland (Dublin)'),
            ('au1', 'Australia (Sydney)'),
        ],
        string='Region',
        default='us1',
        config_parameter='connect_twilio.region',
    )
    connect_twilio_edge = fields.Selection(
        selection=TWILIO_EDGES,
        string='Edge',
        default='ashburn',
        config_parameter='connect_twilio.edge',
    )
    connect_twilio_verify_requests = fields.Boolean(
        string='Verify Twilio Requests',
        default=True,
        config_parameter='connect_twilio.verify_requests',
    )
    connect_twilio_auto_sync = fields.Boolean(
        string='Auto Sync',
        default=True,
        config_parameter='connect_twilio.auto_sync',
    )
    connect_twilio_fetch_call_prices = fields.Boolean(
        string='Fetch Call Prices',
        default=False,
        config_parameter='connect_twilio.fetch_call_prices',
        help='Enable fetching call prices from Twilio API after call completion.',
    )

    def set_values(self):
        super().set_values()
        icp = self.env['ir.config_parameter'].sudo()
        for field_name, key in _BOOLEAN_PARAMS:
            if not self[field_name]:
                icp.set_param(key, 'False')
