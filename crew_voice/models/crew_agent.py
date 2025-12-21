# -*- coding: utf-8 -*-
"""
Crew Agent Voice Extension - Integrates voice.agent.mixin with Crew agents.

This module extends crew.agent to use the voice_base framework, providing
standardized voice configuration and provider-agnostic voice AI capabilities.
"""
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class CrewAgent(models.Model):
    """
    Extend crew.agent with voice.agent.mixin for standardized voice configuration.

    The is_voice_enabled field already exists in crew.agent.
    This extension adds all standardized voice configuration from voice.agent.mixin.
    """
    _inherit = ['crew.agent', 'voice.agent.mixin']

    # === Provider Selection ===
    # The voice_provider_id field comes from voice.agent.mixin
    # We can add a related field or computed field if needed for UI

    # === Computed Voice Prompt ===
    @api.depends('role', 'goal', 'backstory', 'conversational_prompt', 'system_prompt')
    def _compute_full_voice_prompt(self):
        """
        Override the existing computation to use voice.agent.mixin's system_prompt.

        Combines Crew role/goal/backstory with voice-specific prompts.
        """
        for record in self:
            parts = []

            # Base Crew identity
            if record.role:
                parts.append(f"Role: {record.role}")
            if record.goal:
                parts.append(f"Goal: {record.goal}")
            if record.backstory:
                parts.append(f"Backstory: {record.backstory}")

            # Voice-specific prompt from mixin
            if record.system_prompt:
                parts.append(f"Voice Instructions: {record.system_prompt}")

            # Legacy conversational_prompt (if still used)
            if record.conversational_prompt:
                parts.append(f"Additional: {record.conversational_prompt}")

            record.full_voice_prompt = '\n\n'.join(parts) if parts else ''

    # === Conversation Count ===
    def _compute_conversation_count(self):
        """
        Count voice conversations for this agent.

        Uses voice.conversation with generic relation.
        """
        for record in self:
            if record.id:
                # Count conversations where res_model='crew.agent' and res_id=record.id
                record.conversation_count = self.env['voice.conversation'].search_count([
                    ('res_model', '=', 'crew.agent'),
                    ('res_id', '=', record.id),
                ])
            else:
                record.conversation_count = 0

    # === Actions ===
    def action_view_voice_conversations(self):
        """Open voice conversations for this agent."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Voice Conversations'),
            'res_model': 'voice.conversation',
            'view_mode': 'list,form',
            'domain': [
                ('res_model', '=', 'crew.agent'),
                ('res_id', '=', self.id),
            ],
            'context': {
                'default_res_model': 'crew.agent',
                'default_res_id': self.id,
            },
        }

    def action_start_voice_conversation(self):
        """
        Start a voice conversation with this agent in the browser.

        This would typically open a UI component for voice chat.
        """
        self.ensure_one()

        if not self.is_voice_enabled:
            raise ValidationError(_('Voice conversations are not enabled for this agent.'))

        if not self.voice_provider_id:
            raise ValidationError(_('Please configure a voice provider before starting a conversation.'))

        # Return action to open voice conversation UI
        # This would be handled by frontend JavaScript
        return {
            'type': 'ir.actions.client',
            'tag': 'crew_voice_conversation',
            'params': {
                'agent_id': self.id,
                'agent_name': self.name,
                'provider_id': self.voice_provider_id.id,
            },
        }

    def action_sync_to_provider(self):
        """
        Sync this agent to the configured voice provider.

        Delegates to the provider's sync implementation.
        """
        self.ensure_one()

        if not self.is_voice_enabled:
            raise ValidationError(_('Voice is not enabled for this agent.'))

        if not self.voice_provider_id:
            raise ValidationError(_('Please configure a voice provider before syncing.'))

        # Get the provider implementation
        provider = self.voice_provider_id

        # Call the provider's sync method
        if hasattr(provider, 'sync_agent'):
            provider.sync_agent(self)
        else:
            raise ValidationError(
                _('Provider %s does not support agent synchronization.', provider.name)
            )

    # === Onchange Handlers ===
    @api.onchange('is_voice_enabled')
    def _onchange_is_voice_enabled(self):
        """Clear voice provider when voice is disabled."""
        if not self.is_voice_enabled:
            self.voice_provider_id = False

    # === Constraints ===
    @api.constrains('is_voice_enabled', 'voice_provider_id')
    def _check_voice_config(self):
        """Ensure voice provider is set when voice is enabled."""
        for record in self:
            if record.is_voice_enabled and not record.voice_provider_id:
                raise ValidationError(
                    _('Please select a voice provider when enabling voice for agent "%s".', record.name)
                )
