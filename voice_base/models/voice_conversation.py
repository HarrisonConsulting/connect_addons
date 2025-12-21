# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from .constants import CONVERSATION_STATE_LIST, CONVERSATION_SOURCE_LIST


class VoiceConversation(models.Model):
    """
    Universal conversation tracking for voice AI.

    This model tracks all voice conversations regardless of provider,
    storing transcript, metadata, and tool call history.
    """
    _name = 'voice.conversation'
    _description = 'Voice AI Conversation'
    _order = 'create_date desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    # === Identity ===
    name = fields.Char(
        string='Name',
        compute='_compute_name',
        store=True,
        help='Auto-generated conversation name'
    )
    active = fields.Boolean(
        string='Active',
        default=True,
        help='Whether this conversation is active'
    )

    # === Provider Link ===
    voice_provider_id = fields.Many2one(
        comodel_name='voice.provider',
        string='Provider',
        required=False,
        ondelete='restrict',
        help='Voice provider that handled this conversation'
    )
    external_conversation_id = fields.Char(
        string='External Conversation ID',
        readonly=True,
        help='ID of the conversation in the external provider system'
    )

    # === State ===
    state = fields.Selection(
        selection=CONVERSATION_STATE_LIST,
        string='State',
        required=True,
        default='pending',
        tracking=True,
        help='Current state of the conversation'
    )
    source = fields.Selection(
        selection=CONVERSATION_SOURCE_LIST,
        string='Source',
        required=True,
        default='browser',
        help='How this conversation was initiated'
    )

    # === Timing ===
    start_time = fields.Datetime(
        string='Start Time',
        help='When the conversation started'
    )
    end_time = fields.Datetime(
        string='End Time',
        help='When the conversation ended'
    )
    duration_seconds = fields.Integer(
        string='Duration (seconds)',
        compute='_compute_duration',
        store=True,
        help='Total conversation duration in seconds'
    )

    # === Content ===
    transcript = fields.Text(
        string='Transcript',
        help='Full conversation transcript'
    )
    summary = fields.Text(
        string='Summary',
        help='AI-generated conversation summary'
    )
    turn_count = fields.Integer(
        string='Turns',
        default=0,
        help='Number of conversation turns'
    )
    word_count = fields.Integer(
        string='Words',
        default=0,
        help='Total word count in transcript'
    )

    # === Audio ===
    audio_file = fields.Binary(
        string='Audio Recording',
        attachment=True,
        help='Audio recording of the conversation'
    )
    audio_filename = fields.Char(
        string='Audio Filename',
        help='Filename for audio download'
    )
    audio_url = fields.Char(
        string='Audio URL',
        help='URL to stream/download the audio'
    )

    # === Telephony (for phone conversations) ===
    phone_number_from = fields.Char(
        string='From Number',
        help='Phone number that initiated the call (for phone conversations)'
    )
    phone_number_to = fields.Char(
        string='To Number',
        help='Phone number that received the call (for phone conversations)'
    )
    call_sid = fields.Char(
        string='Call SID',
        help='Telephony provider call identifier'
    )

    # === Generic Relation (for linking to any agent model) ===
    res_model = fields.Char(
        string='Related Model',
        help='The model of the agent that handled this conversation (e.g., connect.voice.agent, crew.agent)'
    )
    res_id = fields.Integer(
        string='Related Record ID',
        help='The ID of the agent record that handled this conversation'
    )

    # === Relations ===
    partner_id = fields.Many2one(
        comodel_name='res.partner',
        string='Contact',
        ondelete='set null',
        help='Contact associated with this conversation'
    )
    user_id = fields.Many2one(
        comodel_name='res.users',
        string='User',
        default=lambda self: self.env.user,
        help='Odoo user who initiated/owns this conversation'
    )

    # === Tool Calls ===
    tool_call_ids = fields.One2many(
        comodel_name='voice.conversation.tool_call',
        inverse_name='conversation_id',
        string='Tool Calls',
        help='Tools that were called during this conversation'
    )
    tool_call_count = fields.Integer(
        string='Tool Calls',
        compute='_compute_tool_call_count',
        help='Number of tool calls made'
    )

    # === Quality Metrics ===
    sentiment = fields.Selection(
        selection=[
            ('positive', 'Positive'),
            ('neutral', 'Neutral'),
            ('negative', 'Negative'),
        ],
        string='Sentiment',
        help='Overall conversation sentiment'
    )
    customer_satisfaction = fields.Integer(
        string='Customer Satisfaction',
        help='Customer satisfaction score (0-10)'
    )

    # === Error Tracking ===
    error_message = fields.Text(
        string='Error Message',
        help='Error message if conversation failed'
    )
    error_code = fields.Char(
        string='Error Code',
        help='Error code from provider'
    )

    @api.depends('source', 'create_date')
    def _compute_name(self):
        """Compute conversation name."""
        for conversation in self:
            if conversation.create_date:
                date_str = fields.Datetime.to_string(conversation.create_date)[:16]
                source_label = dict(CONVERSATION_SOURCE_LIST).get(
                    conversation.source, conversation.source or 'unknown'
                )
                conversation.name = f"{source_label} - {date_str}"
            else:
                conversation.name = _('New Conversation')

    @api.depends('start_time', 'end_time')
    def _compute_duration(self):
        """Compute conversation duration."""
        for conversation in self:
            if conversation.start_time and conversation.end_time:
                delta = conversation.end_time - conversation.start_time
                conversation.duration_seconds = int(delta.total_seconds())
            else:
                conversation.duration_seconds = 0

    @api.depends('tool_call_ids')
    def _compute_tool_call_count(self):
        """Compute tool call count."""
        for conversation in self:
            conversation.tool_call_count = len(conversation.tool_call_ids)

    def action_view_transcript(self):
        """Action to view full transcript."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Conversation Transcript'),
            'res_model': 'voice.conversation',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }


class VoiceConversationToolCall(models.Model):
    """
    Tool calls made during a conversation.

    Tracks which tools were called, with what parameters, and what results
    were returned.
    """
    _name = 'voice.conversation.tool_call'
    _description = 'Voice Conversation Tool Call'
    _order = 'timestamp'

    # === Relations ===
    conversation_id = fields.Many2one(
        comodel_name='voice.conversation',
        string='Conversation',
        required=True,
        ondelete='cascade',
        help='The conversation this tool call belongs to'
    )
    tool_id = fields.Many2one(
        comodel_name='voice.tool',
        string='Tool',
        ondelete='set null',
        help='The tool that was called (if from voice.tool)'
    )

    # === Identity ===
    tool_name = fields.Char(
        string='Tool Name',
        required=True,
        help='Name of the tool that was called'
    )
    tool_call_id = fields.Char(
        string='Tool Call ID',
        help='External tool call identifier from provider'
    )

    # === Timing ===
    timestamp = fields.Datetime(
        string='Timestamp',
        required=True,
        default=fields.Datetime.now,
        help='When the tool was called'
    )
    duration_ms = fields.Integer(
        string='Duration (ms)',
        help='How long the tool call took in milliseconds'
    )

    # === Data ===
    parameters = fields.Text(
        string='Parameters',
        help='JSON parameters passed to the tool'
    )
    result = fields.Text(
        string='Result',
        help='JSON result returned from the tool'
    )

    # === Status ===
    success = fields.Boolean(
        string='Success',
        default=True,
        help='Whether the tool call was successful'
    )
    error_message = fields.Text(
        string='Error Message',
        help='Error message if tool call failed'
    )
