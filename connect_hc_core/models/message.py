# -*- coding: utf-8 -*-
"""Thread membership for messages.

connect.conversation groups inbound and outbound messages into a thread; the
back-pointer that makes a message a member of one lives with the thread.
"""

from odoo import fields, models


class Message(models.Model):
    _name = 'connect.message'
    _inherit = ['connect.message']

    conversation_id = fields.Many2one(
        'connect.conversation', string='Conversation', index=True,
        ondelete='set null',
        help='Thread this message belongs to.')
