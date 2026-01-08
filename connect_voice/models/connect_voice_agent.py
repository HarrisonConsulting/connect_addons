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

    @api.model
    def action_import_from_provider(self, provider_id=None):
        """
        Import agents from a voice provider into Odoo.

        Fetches all agents from the provider and creates/updates
        corresponding local records.

        Args:
            provider_id: Optional provider record ID. If not specified,
                        imports from all configured ElevenLabs providers.

        Returns:
            dict: {'created': int, 'updated': int, 'errors': list}
        """
        if provider_id:
            providers = self.env['voice.provider.elevenlabs'].browse(provider_id)
        else:
            providers = self.env['voice.provider.elevenlabs'].search([
                ('provider_type', '=', 'elevenlabs'),
                ('active', '=', True),
            ])

        if not providers:
            raise ValidationError('No active ElevenLabs provider found.')

        created_count = 0
        updated_count = 0
        errors = []

        for provider in providers:
            try:
                # List agents from provider
                remote_agents = provider.list_agents()

                # Get existing agents by external_agent_id
                existing = {
                    a.external_agent_id: a
                    for a in self.search([
                        ('external_agent_id', '!=', False),
                        ('voice_provider_id', '=', provider.id),
                    ])
                }

                for agent_data in remote_agents:
                    agent_id = agent_data.get('agent_id')
                    if not agent_id:
                        continue

                    try:
                        vals = self._prepare_vals_from_provider(agent_data, provider)

                        if agent_id in existing:
                            existing[agent_id].with_context(skip_provider_sync=True).write(vals)
                            updated_count += 1
                        else:
                            vals['external_agent_id'] = agent_id
                            vals['voice_provider_id'] = provider.id
                            vals['sync_status'] = 'synced'
                            self.with_context(skip_provider_sync=True).create(vals)
                            created_count += 1

                    except Exception as e:
                        errors.append(f"Agent {agent_data.get('name', agent_id)}: {e}")

            except Exception as e:
                errors.append(f"Provider {provider.name}: {e}")

        # Notify user
        msg_parts = []
        if created_count:
            msg_parts.append(f"Created {created_count} agent(s)")
        if updated_count:
            msg_parts.append(f"Updated {updated_count} agent(s)")
        if errors:
            msg_parts.append(f"Errors: {len(errors)}")

        result_msg = ". ".join(msg_parts) if msg_parts else "No agents found"

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Agent Import',
                'message': result_msg,
                'type': 'success' if not errors else 'warning',
                'sticky': bool(errors),
            }
        }

    def _prepare_vals_from_provider(self, agent_data, provider):
        """
        Prepare field values from provider agent data.

        Args:
            agent_data: Dict of agent data from provider
            provider: Provider record

        Returns:
            dict: Field values for create/write
        """
        vals = {
            'name': agent_data.get('name', 'Imported Agent'),
        }

        # Extract conversation config if available
        conv_config = agent_data.get('conversation_config', {})
        if conv_config:
            agent_config = conv_config.get('agent', {})
            if agent_config:
                if agent_config.get('prompt', {}).get('prompt'):
                    vals['system_prompt'] = agent_config['prompt']['prompt']
                if agent_config.get('first_message'):
                    vals['first_message'] = agent_config['first_message']
                if agent_config.get('language'):
                    vals['language'] = agent_config['language']

            llm_config = conv_config.get('llm', {})
            if llm_config:
                if llm_config.get('model_id'):
                    vals['llm_model'] = llm_config['model_id']
                if llm_config.get('temperature'):
                    vals['temperature'] = llm_config['temperature']
                if llm_config.get('max_tokens'):
                    vals['max_tokens'] = llm_config['max_tokens']

            tts_config = conv_config.get('tts', {})
            if tts_config:
                voice_id = tts_config.get('voice_id')
                if voice_id:
                    # Try to find matching voice
                    voice = self.env['voice.voice'].search([
                        ('external_voice_id', '=', voice_id),
                        ('voice_provider_id', '=', provider.id),
                    ], limit=1)
                    if voice:
                        vals['voice_id'] = voice.id

        return vals

    # === Constraints ===
    @api.constrains('exten_id', 'callflow_id')
    def _check_connect_config(self):
        """Ensure at least one of extension or callflow is configured."""
        for record in self:
            if not record.exten_id and not record.callflow_id:
                raise ValidationError(
                    'Please configure either an Extension or a Callflow for this voice agent.'
                )
