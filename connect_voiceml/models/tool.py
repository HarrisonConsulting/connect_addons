# -*- coding: utf-8 -*-
from odoo import fields, models


class Tool(models.Model):
    _inherit = 'connect.tool'

    adapter = fields.Selection(
        selection_add=[('voiceml', 'VoiceML')],
        ondelete={'voiceml': 'set null'},
    )
