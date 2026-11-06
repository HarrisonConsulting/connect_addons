# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    connect_panel_recording = fields.Boolean(
        string='Record from the phone panel',
        default=False,
        help='Off by default. Lets people start recording a call from the phone '
             'panel. Turning this on needs consent wording and approval first. '
             'It does not change automatic recording settings.')
