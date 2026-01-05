# -*- coding: utf-8 -*-
"""
ElevenLabs Connect Settings Extension.

Extends connect.settings with ElevenLabs-specific configuration fields
for Connect telephony integration.
"""
import logging
import uuid
from urllib.parse import urljoin

from odoo import fields, models, api
from odoo.addons.connect.models.settings import PROTECTED_FIELDS
from odoo.exceptions import ValidationError

logger = logging.getLogger(__name__)

# Register protected fields for masking
PROTECTED_FIELDS.append('display_elevenlabs_api_key')
PROTECTED_FIELDS.append('display_elevenlabs_webhook_secret')


class ElevenLabsConnectSettings(models.Model):
    """
    Extend connect.settings with ElevenLabs configuration.

    Provides configuration fields and sync methods for the ElevenLabs
    voice provider when used with Connect telephony.
    """
    _inherit = 'connect.settings'

    # API Configuration (for convenience - mirrors provider api_key)
    elevenlabs_api_key = fields.Char(
        string='ElevenLabs API Key',
        groups="base.group_erp_manager",
        help="Your ElevenLabs API key for authentication"
    )
    display_elevenlabs_api_key = fields.Char(
        string='API Key',
        help="Display field for API key (masked)"
    )
    elevenlabs_enabled = fields.Boolean(
        string='ElevenLabs Enabled',
        help="Enable ElevenLabs integration"
    )

    # Agent Configuration
    elevenlabs_agent_token = fields.Char(
        string='Agent Token',
        required=True,
        groups="base.group_erp_manager",
        default=lambda self: str(uuid.uuid4()),
        help="Security token for agent communication"
    )
    elevenlabs_agent_url = fields.Char(
        string='Agent Stream URL',
        required=True,
        default='wss://api.elevenlabs.io/v1/convai/conversation',
        help="WebSocket URL for ElevenLabs Conversational AI streaming"
    )

    # Webhook Configuration
    elevenlabs_webhook_url = fields.Char(
        string='Post-Call Webhook URL',
        compute='_compute_elevenlabs_webhook_url',
        help="URL for receiving post-call webhooks from ElevenLabs"
    )
    elevenlabs_webhook_secret = fields.Char(
        string='Webhook Secret',
        groups="base.group_erp_manager",
        help="Secret for validating webhook signatures"
    )
    display_elevenlabs_webhook_secret = fields.Char(
        string='Webhook Secret',
        help="Display field for webhook secret (masked)"
    )

    # Default Voice Configuration
    elevenlabs_default_voice_id = fields.Many2one(
        'voice.voice',
        string='Default Voice',
        domain="[('voice_provider_id.provider_type', '=', 'elevenlabs')]",
        ondelete='set null',
        help="Default voice to use for ElevenLabs TTS"
    )

    # Provider Reference
    elevenlabs_provider_id = fields.Many2one(
        'voice.provider',
        string='ElevenLabs Provider',
        compute='_compute_elevenlabs_provider',
        help="The ElevenLabs voice provider record"
    )

    def _compute_elevenlabs_webhook_url(self):
        """Compute the post-call webhook URL."""
        api_url = self.sudo().get_param('api_url') or ''
        for rec in self:
            rec.elevenlabs_webhook_url = urljoin(api_url, 'voice/webhook/post_call')

    def _compute_elevenlabs_provider(self):
        """Get the ElevenLabs provider record."""
        for rec in self:
            rec.elevenlabs_provider_id = False

        try:
            Provider = self.env['voice.provider'].sudo()
            provider = Provider.search([('provider_type', '=', 'elevenlabs')], limit=1)
            for rec in self:
                rec.elevenlabs_provider_id = provider.id if provider else False
        except Exception as e:
            # voice_base may not be properly installed (missing table)
            logger.warning("Could not fetch ElevenLabs provider: %s", e)

    def open_elevenlabs_settings(self):
        """Open ElevenLabs settings form."""
        rec = self.search([])
        if not rec:
            rec = self.sudo().with_context(no_constrains=True).create({})
        else:
            rec = rec[0]
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'connect.settings',
            'res_id': rec.id,
            'name': 'ElevenLabs Settings',
            'view_mode': 'form',
            'view_id': self.env.ref('voice_elevenlabs_connect.elevenlabs_settings_form').id,
            'target': 'current',
        }

    def get_elevenlabs_client(self):
        """
        Get authenticated ElevenLabs API client.

        Returns:
            ElevenLabs: Authenticated client instance

        Raises:
            ValidationError: If API key not configured
        """
        key = self.sudo().get_param('elevenlabs_api_key')
        if not key:
            raise ValidationError('ElevenLabs API key not configured!')
        try:
            from elevenlabs import ElevenLabs
            return ElevenLabs(api_key=key)
        except ImportError:
            raise ValidationError('ElevenLabs SDK not installed. Run: pip install elevenlabs')

    def get_elevenlabs_provider(self):
        """
        Get or create the ElevenLabs voice provider record.

        Returns:
            voice.provider: ElevenLabs provider record
        """
        Provider = self.env['voice.provider'].sudo()
        provider = Provider.search([('provider_type', '=', 'elevenlabs')], limit=1)
        if not provider:
            provider = Provider.create({
                'name': 'ElevenLabs',
                'provider_type': 'elevenlabs',
                'is_active': True,
            })
        return provider

    def elevenlabs_sync_voices(self):
        """Sync voices from ElevenLabs API."""
        provider = self.get_elevenlabs_provider()
        if hasattr(provider, 'sync_voices'):
            provider.sync_voices()
            self.connect_notify('Voices synced successfully', title='ElevenLabs')
        else:
            raise ValidationError('Provider does not support voice sync')

    def elevenlabs_sync_agents(self):
        """Sync all voice agents to ElevenLabs."""
        provider = self.get_elevenlabs_provider()
        Agent = self.env['connect.voice.agent'].sudo()
        agents = Agent.search([('voice_provider_id', '=', provider.id)])
        for agent in agents:
            if hasattr(provider, 'sync_agent'):
                provider.sync_agent(agent)
        self.connect_notify(f'Synced {len(agents)} agents', title='ElevenLabs')

    def elevenlabs_reset_token(self):
        """Generate a new agent security token."""
        self.set_param('elevenlabs_agent_token', str(uuid.uuid4()))
        self.connect_notify('Agent token regenerated', title='ElevenLabs')

    def elevenlabs_full_sync(self):
        """Run full synchronization with ElevenLabs."""
        self.elevenlabs_sync_voices()
        self.elevenlabs_sync_agents()
        self.connect_notify('Full sync completed', title='ElevenLabs')

    def elevenlabs_test_connection(self):
        """Test ElevenLabs API connection."""
        try:
            client = self.get_elevenlabs_client()
            # Test by fetching voices (lightweight API call)
            voices = client.voices.get_all()
            voice_count = len(voices.voices) if hasattr(voices, 'voices') else 0
            self.connect_notify(
                f'Connection successful! Found {voice_count} voices.',
                title='ElevenLabs'
            )
        except Exception as e:
            raise ValidationError(f'Connection failed: {str(e)}')
