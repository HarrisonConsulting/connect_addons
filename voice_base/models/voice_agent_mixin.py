# -*- coding: utf-8 -*-
from odoo import models, fields, api
from .constants import LLM_MODEL_LIST, TTS_MODEL_LIST, LANGUAGE_LIST, SYNC_STATUS_LIST


class VoiceAgentMixin(models.AbstractModel):
    """
    Mixin providing common voice agent configuration fields.

    Any model that needs voice agent capabilities should inherit from this mixin.
    It provides all the standard configuration options for voice, LLM, and
    conversation settings that are provider-agnostic.
    """
    _name = 'voice.agent.mixin'
    _description = 'Voice Agent Configuration Mixin'

    # === Provider Link ===
    voice_provider_id = fields.Many2one(
        comodel_name='voice.provider',
        string='Voice Provider',
        ondelete='restrict',
        help='The voice AI provider to use for this agent'
    )

    # === Voice Configuration ===
    voice_id = fields.Many2one(
        comodel_name='voice.voice',
        string='Voice',
        ondelete='restrict',
        help='The voice to use for text-to-speech'
    )
    tts_model = fields.Selection(
        selection=TTS_MODEL_LIST,
        string='TTS Model',
        default='eleven_multilingual_v2',
        help='Text-to-speech model to use'
    )
    stability = fields.Float(
        string='Stability',
        default=0.5,
        help='Voice stability (0.0-1.0). Higher = more consistent, lower = more variable'
    )
    similarity_boost = fields.Float(
        string='Similarity Boost',
        default=0.8,
        help='Voice similarity enhancement (0.0-1.0). Higher = closer to original voice'
    )
    speed = fields.Float(
        string='Speed',
        default=1.0,
        help='Speech speed multiplier (0.25-4.0). 1.0 = normal speed'
    )
    optimize_streaming_latency = fields.Integer(
        string='Optimize Streaming Latency',
        default=3,
        help='Latency optimization level (0-4). Higher = lower latency but may reduce quality'
    )

    # === LLM Configuration ===
    llm_model = fields.Selection(
        selection=LLM_MODEL_LIST,
        string='LLM Model',
        default='gpt-4o',
        help='Large Language Model to use for conversation'
    )
    temperature = fields.Float(
        string='Temperature',
        default=0.7,
        help='LLM creativity (0.0-2.0). Higher = more creative, lower = more focused'
    )
    max_tokens = fields.Integer(
        string='Max Tokens',
        default=500,
        help='Maximum tokens in LLM response'
    )
    system_prompt = fields.Text(
        string='System Prompt',
        help='Instructions for the AI agent behavior and personality'
    )

    # === Conversation Settings ===
    first_message = fields.Text(
        string='First Message',
        help='Initial greeting message from the agent'
    )
    language = fields.Selection(
        selection=LANGUAGE_LIST,
        string='Language',
        default='en',
        help='Primary language for the conversation'
    )
    turn_timeout = fields.Integer(
        string='Turn Timeout (seconds)',
        default=10,
        help='How long to wait for user to speak before considering turn complete'
    )
    max_duration_seconds = fields.Integer(
        string='Max Duration (seconds)',
        default=600,
        help='Maximum conversation duration in seconds'
    )
    background_sound = fields.Selection(
        selection=[
            ('off', 'Off'),
            ('office', 'Office'),
        ],
        string='Background Sound',
        default='off',
        help='Background ambient sound to play during conversation'
    )
    enable_backchannel = fields.Boolean(
        string='Enable Backchannel',
        default=True,
        help='Allow agent to make acknowledgment sounds (mm-hmm, uh-huh) while listening'
    )

    # === Tool Integration ===
    tool_ids = fields.Many2many(
        comodel_name='voice.tool',
        string='Tools',
        help='Custom tools/functions available to the agent'
    )

    # === MCP Server Integration ===
    mcp_server_ids = fields.Many2many(
        comodel_name='voice.mcp.server',
        string='MCP Servers',
        help='Model Context Protocol servers providing additional capabilities'
    )

    # === Knowledge Base ===
    knowledge_base_id = fields.Many2one(
        comodel_name='voice.knowledge.base',
        string='Knowledge Base',
        ondelete='restrict',
        help='Knowledge base for RAG (Retrieval-Augmented Generation)'
    )

    # === Sync Status (for providers that sync to external platforms) ===
    external_agent_id = fields.Char(
        string='External Agent ID',
        readonly=True,
        help='ID of the agent in the external provider system'
    )
    sync_status = fields.Selection(
        selection=SYNC_STATUS_LIST,
        string='Sync Status',
        default='pending',
        readonly=True,
        help='Synchronization status with external provider'
    )
    sync_error = fields.Text(
        string='Sync Error',
        readonly=True,
        help='Last synchronization error message'
    )
    last_sync = fields.Datetime(
        string='Last Sync',
        readonly=True,
        help='When the agent was last synchronized with the provider'
    )

    # === Computed Fields ===
    has_tools = fields.Boolean(
        string='Has Tools',
        compute='_compute_has_integrations',
        store=False,
        help='Whether this agent has custom tools configured'
    )
    has_mcp_servers = fields.Boolean(
        string='Has MCP Servers',
        compute='_compute_has_integrations',
        store=False,
        help='Whether this agent has MCP servers configured'
    )
    has_knowledge_base = fields.Boolean(
        string='Has Knowledge Base',
        compute='_compute_has_integrations',
        store=False,
        help='Whether this agent has a knowledge base configured'
    )

    @api.depends('tool_ids', 'mcp_server_ids', 'knowledge_base_id')
    def _compute_has_integrations(self):
        """Compute integration flags."""
        for record in self:
            record.has_tools = bool(record.tool_ids)
            record.has_mcp_servers = bool(record.mcp_server_ids)
            record.has_knowledge_base = bool(record.knowledge_base_id)

    @api.onchange('voice_provider_id')
    def _onchange_voice_provider_id(self):
        """Clear voice when provider changes."""
        if self.voice_provider_id:
            self.voice_id = False

    @api.onchange('llm_model')
    def _onchange_llm_model(self):
        """Adjust max_tokens based on model."""
        if self.llm_model:
            # Different models have different context windows
            # These are reasonable defaults
            if 'gpt-4' in self.llm_model:
                self.max_tokens = 1000
            elif 'claude' in self.llm_model:
                self.max_tokens = 1000
            elif 'gemini' in self.llm_model:
                self.max_tokens = 2000
            else:
                self.max_tokens = 500

    def _build_custom_llm_config(self):
        """
        Build custom LLM configuration for voice agents.

        This is a stub method that returns None by default.
        Install voice_elevenlabs_openai to enable custom LLM support
        via OpenAI-compatible endpoints.

        Returns:
            dict or None: Custom LLM configuration dict with keys:
                - url: Chat completions endpoint URL
                - model_id: Model identifier
                - api_key: API key (optional)
                - extra_body: Additional request params (optional)
        """
        return None
