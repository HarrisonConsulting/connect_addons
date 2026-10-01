from odoo import fields, models


class Campaign(models.Model):
    _name = 'connect.campaign'
    _description = 'Voice campaign'
    _order = 'name, id'

    name = fields.Char(
        required=True,
        help='The name a person gives this campaign.',
    )
    tool_id = fields.Many2one(
        'connect.tool', required=True, ondelete='restrict',
        help='The system this campaign places calls on.',
    )
    user_id = fields.Many2one(
        'connect.user', ondelete='set null',
        help='The person this campaign runs as. Empty uses the caller.',
    )

    def originate_context(self):
        self.ensure_one()
        return {
            'connect_tool_id': self.tool_id.id,
            'connect_campaign_id': self.id,
        }
