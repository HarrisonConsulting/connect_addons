# -*- coding: utf-8 -*-
"""Audio-layer wiring for calls.

A call gets its spoken prompts from the TTS abstraction, and a voicemail
left on it is filed into a box and moved through a stage. Both are audio
layer concerns declared on top of the Twilio-shaped call record.
"""

from odoo import fields, models


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

    def _group_expand_voicemail_stage(self, stages, domain):
        return stages.search([])
