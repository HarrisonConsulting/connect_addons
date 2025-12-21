# -*- coding: utf-8 -*-
"""
Connect Voice Agent - Voice AI agent for Connect telephony.

Integrates the voice_base framework with Connect extensions and callflows,
providing voice AI capabilities for Twilio-based telephony.
"""
from odoo import models, fields, api
from odoo.exceptions import ValidationError


class ConnectVoiceAgent(models.Model):
    _name = 'connect.voice.agent'
    _description = 'Connect Voice Agent'
    _inherit = ['voice.agent.mixin', 'mail.thread', 'mail.activity.mixin']
    _order = 'name'

    # === Identity ===
    name = fields.Char(
        string='Agent Name',
        required=True,
        tracking=True,
        help='Name of the voice agent'
    )
    active = fields.Boolean(
        default=True,
        tracking=True,
        help='Whether this agent is active'
    )

    # === Connect Integration ===
    exten_id = fields.Many2one(
        comodel_name='connect.exten',
        string='Extension',
        ondelete='restrict',
        tracking=True,
        help='Connect extension to attach this voice agent to'
    )
    callflow_id = fields.Many2one(
        comodel_name='connect.callflow',
        string='Callflow',
        ondelete='restrict',
        tracking=True,
        help='Connect callflow to use for this voice agent'
    )

    # === Rendering ===
    render_type = fields.Selection(
        selection=[
            ('stream', 'Stream'),
            ('redirect', 'Redirect'),
        ],
        string='Render Type',
        default='stream',
        required=True,
        tracking=True,
        help='How the agent renders responses: Stream = WebSocket streaming, Redirect = HTTP redirect'
    )

    # === Webhooks ===
    agent_url = fields.Char(
        string='Agent URL',
        help='WebSocket URL for Twilio to connect to this agent'
    )
    post_call_webhook_url = fields.Char(
        string='Post-Call Webhook URL',
        compute='_compute_post_call_webhook_url',
        store=False,
        help='URL for post-call webhook notifications'
    )
    post_call_webhook_secret = fields.Char(
        string='Post-Call Webhook Secret',
        groups='base.group_system',
        help='Secret token for authenticating post-call webhook requests'
    )

    # === Status ===
    conversation_count = fields.Integer(
        string='Conversations',
        compute='_compute_conversation_count',
        store=False,
        help='Number of conversations this agent has handled'
    )

    # === Computed Fields ===
    def _compute_post_call_webhook_url(self):
        """Compute the post-call webhook URL."""
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        for record in self:
            if record.id:
                record.post_call_webhook_url = f"{base_url}/connect/voice/webhook/{record.id}/post_call"
            else:
                record.post_call_webhook_url = False

    def _compute_conversation_count(self):
        """Count conversations for this agent."""
        for record in self:
            # Count conversations linked to this agent
            # The voice.conversation model has a generic relation field
            # We need to count conversations where res_model='connect.voice.agent' and res_id=record.id
            if record.id:
                record.conversation_count = self.env['voice.conversation'].search_count([
                    ('res_model', '=', 'connect.voice.agent'),
                    ('res_id', '=', record.id),
                ])
            else:
                record.conversation_count = 0

    # === Actions ===
    def action_view_conversations(self):
        """Open conversations for this agent."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Conversations',
            'res_model': 'voice.conversation',
            'view_mode': 'list,form',
            'domain': [
                ('res_model', '=', 'connect.voice.agent'),
                ('res_id', '=', self.id),
            ],
            'context': {
                'default_res_model': 'connect.voice.agent',
                'default_res_id': self.id,
            },
        }

    def action_sync_to_provider(self):
        """
        Sync this agent to the configured voice provider.

        Delegates to the provider's sync implementation.
        """
        self.ensure_one()
        if not self.voice_provider_id:
            raise ValidationError('Please configure a voice provider before syncing.')

        # Get the provider implementation
        provider = self.voice_provider_id

        # Call the provider's sync method
        # The provider model should implement this method
        if hasattr(provider, 'sync_agent'):
            provider.sync_agent(self)
        else:
            raise ValidationError(
                f'Provider {provider.name} does not support agent synchronization.'
            )

    # === Constraints ===
    @api.constrains('exten_id', 'callflow_id')
    def _check_connect_config(self):
        """Ensure at least one of extension or callflow is configured."""
        for record in self:
            if not record.exten_id and not record.callflow_id:
                raise ValidationError(
                    'Please configure either an Extension or a Callflow for this voice agent.'
                )
