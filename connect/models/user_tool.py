from odoo import api, fields, models, release

if release.version_info[0] >= 19:
    from odoo.models import Constraint


class UserTool(models.Model):
    _name = 'connect.user.tool'
    _description = 'Voice system assignment'
    _order = 'sequence, id'

    user_id = fields.Many2one(
        'connect.user', required=True, ondelete='cascade',
        help='Person this system is assigned to.',
    )
    tool_id = fields.Many2one(
        'connect.tool', required=True, ondelete='restrict',
        help='System assigned to this person.',
    )
    role = fields.Selection(
        [('active', 'Active'), ('backup', 'Backup')],
        required=True, default='active',
        help='Active systems are used together. The next row is the backup.',
    )
    sequence = fields.Integer(
        default=10,
        help='Order inside the same role. Lower numbers come first.',
    )
    use = fields.Selection(
        [('voice', 'Voice'), ('message', 'Message'), ('both', 'Voice and message')],
        required=True, default='both',
        help='Voice, message, or both.',
    )

    if release.version_info[0] >= 19:
        _user_tool_uniq = Constraint(
            'UNIQUE(user_id, tool_id)',
            'This person already has this system.',
        )
    else:
        _sql_constraints = [
            (
                'user_tool_uniq',
                'UNIQUE(user_id, tool_id)',
                'This person already has this system.',
            ),
        ]

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.user_id._sync_provider_mirror()
        return records

    def write(self, vals):
        users = self.user_id
        res = super().write(vals)
        (users | self.user_id)._sync_provider_mirror()
        return res

    def unlink(self):
        users = self.user_id
        res = super().unlink()
        users._sync_provider_mirror()
        return res
