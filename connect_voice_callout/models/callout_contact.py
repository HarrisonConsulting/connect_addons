# -*- coding: utf-8 -*-
"""
Connect Voice Callout Contact extension.

Extends callout contacts with voice AI call tracking.
"""
from odoo import models, fields


class VoiceCalloutContact(models.Model):
    """
    Extend connect.callout.contact with voice call tracking.
    """
    _inherit = 'connect.callout.contact'

    # === Voice Conversation Tracking ===
    voice_conversation_id = fields.Char(
        string='Voice Conversation ID',
        readonly=True,
        help='External conversation ID from voice provider'
    )
    voice_summary = fields.Text(
        string='AI Summary',
        readonly=True,
        help='AI-generated summary of the conversation'
    )
    voice_transcript = fields.Text(
        string='Transcript',
        readonly=True,
        help='Full transcript of the conversation'
    )
    voice_call_duration = fields.Integer(
        string='Voice Call Duration',
        readonly=True,
        help='Duration of the voice conversation in seconds'
    )
