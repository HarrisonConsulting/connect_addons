"""Number reputation on the partner."""

from odoo import api, fields, models

from ..services import numbering


class ResPartner(models.Model):
    _inherit = 'res.partner'

    caller_mark_ids = fields.Many2many(
        'connect.caller.mark', compute='_compute_caller_marks',
        string='Number Reports',
        help='Active reports about the partner phone and mobile numbers, with the system that made each.')
    caller_mark_count = fields.Integer(
        compute='_compute_caller_marks',
        help='Number of active reports about the partner numbers.')

    @api.depends('phone', 'mobile')
    def _compute_caller_marks(self):
        country = self.env.company.country_id.code or 'US'
        Mark = self.env['connect.caller.mark']
        for partner in self:
            e164s = {numbering.analyze(raw, country)['e164'] for raw in (partner.phone, partner.mobile) if raw}
            e164s.discard('')
            marks = Mark.search([('reputation_id.e164', 'in', list(e164s)), ('state', '=', 'active')]) \
                if e164s else Mark
            partner.caller_mark_ids = marks
            partner.caller_mark_count = len(marks)

    def action_open_caller_marks(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Number reports'),
            'res_model': 'connect.caller.mark',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.caller_mark_ids.ids)],
        }
