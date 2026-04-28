from odoo import fields, models


class VoicemailStage(models.Model):
    _name = 'connect.voicemail_stage'
    _description = 'Voicemail Stage'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True, help="")
    sequence = fields.Integer(default=50, help="")
    fold = fields.Boolean(help="Folded in kanban view")
    color = fields.Integer(help="")
