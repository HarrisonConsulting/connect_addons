from odoo import fields, models, api
from .audio_referrer_mixin import SELECTABLE_AUDIO_STATES


class VoicemailBox(models.Model):
    _name = 'connect.voicemail_box'
    _description = 'Voicemail Box'
    _order = 'name'
    _inherit = ['mail.thread']

    name = fields.Char(required=True, tracking=True)
    description = fields.Text()
    active = fields.Boolean(default=True)
    color = fields.Integer()
    member_ids = fields.Many2many(
        'res.users', 'connect_voicemail_box_member_rel',
        'box_id', 'user_id', string='Members',
        domain="[('share', '=', False)]",
        help='Users with access to voicemails and call records belonging to this box.')
    member_count = fields.Integer(compute='_compute_counts')
    voicemail_count = fields.Integer(compute='_compute_counts')
    voicemail_audio_id = fields.Many2one(
        'connect.audio', ondelete='set null',
        domain=[('state', 'in', SELECTABLE_AUDIO_STATES)],
        string='Default Voicemail Prompt',
        help='Default voicemail prompt audio for boxes. Overridden by the user/callflow audio when set there.')

    @api.depends('member_ids')
    def _compute_counts(self):
        Call = self.env['connect.call']
        for rec in self:
            rec.member_count = len(rec.member_ids)
            rec.voicemail_count = Call.search_count([
                ('voicemail_box_id', '=', rec.id),
                ('call_result', '=', 'voicemail'),
            ]) if rec.id else 0

    def action_view_voicemails(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('connect.voicemail_action')
        action['domain'] = [('voicemail_box_id', '=', self.id), ('call_result', '=', 'voicemail')]
        action['context'] = {'default_voicemail_box_id': self.id}
        return action
