# -*- coding: utf-8 -*-
"""
ElevenLabs Connect Settings Extension.

Extends res.config.settings with Connect telephony integration settings
for ElevenLabs voice provider.
"""
import logging
import uuid

from odoo import fields, models, api, _
from odoo.exceptions import UserError

logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    """
    Connect integration settings for ElevenLabs.

    Adds agent runtime configuration and webhook settings.
    """
    _inherit = 'res.config.settings'

    # === Agent Runtime Configuration ===
    elevenlabs_agent_url = fields.Char(
        string='Agent Stream URL',
        config_parameter='voice_elevenlabs_connect.agent_url',
        default='wss://api.elevenlabs.io/v1/convai/conversation',
        help='WebSocket URL for ElevenLabs Conversational AI streaming'
    )
    elevenlabs_agent_token = fields.Char(
        string='Agent Security Token',
        help='Security token for agent communication (stored securely)'
    )

    # === Webhook Configuration ===
    elevenlabs_webhook_url = fields.Char(
        string='Post-Call Webhook URL',
        compute='_compute_elevenlabs_webhook_url',
        help='URL for receiving post-call webhooks from ElevenLabs'
    )
    elevenlabs_webhook_secret = fields.Char(
        string='Webhook Secret',
        help='Secret for validating webhook signatures (stored securely)'
    )

    def _compute_elevenlabs_webhook_url(self):
        """Compute the post-call webhook URL from web.base.url."""
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        for rec in self:
            if base_url:
                rec.elevenlabs_webhook_url = f"{base_url.rstrip('/')}/voice/webhook/post_call"
            else:
                rec.elevenlabs_webhook_url = ''

    @api.model
    def get_values(self):
        """Override to populate secure fields from storage."""
        res = super().get_values()

        TokenStorage = self.env['voice.token.storage']

        # Get agent token from secure storage
        agent_token = TokenStorage.get_secret('elevenlabs_agent_token')
        if agent_token:
            res['elevenlabs_agent_token'] = agent_token

        # Get webhook secret from secure storage
        webhook_secret = TokenStorage.get_secret('elevenlabs_webhook_secret')
        if webhook_secret:
            res['elevenlabs_webhook_secret'] = webhook_secret

        return res

    def set_values(self):
        """Override to store secure fields."""
        super().set_values()

        TokenStorage = self.env['voice.token.storage']

        # Store agent token securely
        if self.elevenlabs_agent_token:
            TokenStorage.store_secret(
                'elevenlabs_agent_token',
                self.elevenlabs_agent_token
            )

        # Store webhook secret securely
        if self.elevenlabs_webhook_secret:
            TokenStorage.store_secret(
                'elevenlabs_webhook_secret',
                self.elevenlabs_webhook_secret
            )

    def action_regenerate_agent_token(self):
        """Generate a new agent security token."""
        self.ensure_one()
        new_token = str(uuid.uuid4())

        self.env['voice.token.storage'].store_secret(
            'elevenlabs_agent_token',
            new_token
        )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Token Regenerated'),
                'message': _('A new agent security token has been generated.'),
                'type': 'success',
                'sticky': False,
            }
        }

    def action_sync_elevenlabs_agents(self):
        """Sync all voice agents to ElevenLabs."""
        Agent = self.env['connect.voice.agent'].sudo()
        agents = Agent.search([('voice_provider_id', '!=', False)])

        synced = 0
        errors = []

        for agent in agents:
            try:
                if hasattr(agent, 'action_sync_to_provider'):
                    agent.action_sync_to_provider()
                    synced += 1
            except Exception as e:
                errors.append(f"{agent.name}: {e}")

        msg = _('Synced %d agents') % synced
        if errors:
            msg += _('\nErrors: %d') % len(errors)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Agent Sync'),
                'message': msg,
                'type': 'warning' if errors else 'success',
                'sticky': bool(errors),
            }
        }
