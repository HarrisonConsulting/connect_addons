# -*- coding: utf-8 -*-
"""Audio-layer wiring for calls.

A call gets its spoken prompts from the TTS abstraction, and a voicemail
left on it is filed into a box and moved through a stage. Both are audio
layer concerns declared on top of the Twilio-shaped call record.
"""

from odoo import Command, fields, models


class Call(models.Model):
    _name = 'connect.call'
    _inherit = ['connect.call', 'connect.tts.mixin']

    voicemail_stage_id = fields.Many2one(
        'connect.voicemail_stage', string='Stage', index=True, tracking=True,
        group_expand='_group_expand_voicemail_stage',
        help='Handling stage of the voicemail left on this call.',
    )
    voicemail_box_id = fields.Many2one(
        'connect.voicemail_box', ondelete='set null', string='Voicemail Box',
        index=True, tracking=True, readonly=True,
        help='Shared box this call belongs to. Set via the user or callflow that received the voicemail.')
    voicemail_assignee_ids = fields.Many2many(
        'res.users', 'connect_call_voicemail_assignee_rel', 'call_id', 'user_id',
        string='Assignees', domain="[('share', '=', False)]",
        help='Internal users responsible for handling this voicemail.',
    )
    voicemail_transcript = fields.Text(
        string='Voicemail Transcript',
        help='Transcript generated for this voicemail.',
    )

    def _group_expand_voicemail_stage(self, stages, domain):
        return stages.search([])

    def action_assign_to_me(self):
        self.ensure_one()
        if self.env.user not in self.voicemail_assignee_ids:
            self.voicemail_assignee_ids = [Command.link(self.env.uid)]

    def action_transcribe(self):
        self.ensure_one()
        if self.recording:
            self.recording.get_transcript()

    def action_view_partner(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'res.partner',
            'res_id': self.partner.id,
            'view_mode': 'form',
            'target': 'current',
        }
