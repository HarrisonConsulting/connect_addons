# -*- coding: utf-8 -*-
"""
ElevenLabs Voice Provider Settings.

Extends res.config.settings with ElevenLabs-specific configuration.
"""
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

logger = logging.getLogger(__name__)

# TTS Model selections
TTS_MODEL_SELECTION = [
    ('eleven_flash_v2_5', 'Flash v2.5 (Fastest)'),
    ('eleven_flash_v2', 'Flash v2'),
    ('eleven_turbo_v2_5', 'Turbo v2.5'),
    ('eleven_turbo_v2', 'Turbo v2'),
    ('eleven_multilingual_v2', 'Multilingual v2'),
]

# LLM Model selections
LLM_MODEL_SELECTION = [
    # GPT Models
    ('gpt-4o-mini', 'GPT 4o Mini'),
    ('gpt-4o', 'GPT 4o'),
    ('gpt-4-turbo', 'GPT 4 Turbo'),
    ('gpt-4.1', 'GPT 4.1'),
    ('gpt-4.1-mini', 'GPT 4.1 Mini'),
    ('gpt-5', 'GPT 5'),
    ('gpt-5.1', 'GPT 5.1'),
    # Claude Models
    ('claude-sonnet-4-5', 'Claude 4.5 Sonnet'),
    ('claude-sonnet-4', 'Claude 4 Sonnet'),
    ('claude-3-7-sonnet', 'Claude 3.7 Sonnet'),
    ('claude-3-5-sonnet', 'Claude 3.5 Sonnet'),
    # Gemini Models
    ('gemini-2.5-flash', 'Gemini 2.5 Flash'),
    ('gemini-2.0-flash', 'Gemini 2.0 Flash'),
    ('gemini-1.5-flash', 'Gemini 1.5 Flash'),
    # Custom
    ('custom-llm', 'Custom LLM'),
]


class ResConfigSettings(models.TransientModel):
    """
    ElevenLabs settings extension.
    """
    _inherit = 'res.config.settings'

    # === ElevenLabs Enable ===
    elevenlabs_enabled = fields.Boolean(
        string='Enable ElevenLabs',
        config_parameter='voice_elevenlabs.enabled',
        default=False,
        help='Enable ElevenLabs voice provider'
    )

    # === API Configuration ===
    elevenlabs_api_key = fields.Char(
        string='ElevenLabs API Key',
        help='Your ElevenLabs API key for authentication'
    )
    elevenlabs_api_endpoint = fields.Char(
        string='API Endpoint',
        config_parameter='voice_elevenlabs.api_endpoint',
        help='Custom API endpoint URL (optional, uses default if not set)'
    )

    # === Default Models ===
    elevenlabs_default_tts_model = fields.Selection(
        selection=TTS_MODEL_SELECTION,
        string='Default TTS Model',
        config_parameter='voice_elevenlabs.default_tts_model',
        default='eleven_flash_v2_5',
        help='Default text-to-speech model'
    )
    elevenlabs_default_llm_model = fields.Selection(
        selection=LLM_MODEL_SELECTION,
        string='Default LLM Model',
        config_parameter='voice_elevenlabs.default_llm_model',
        default='gpt-4o',
        help='Default language model for agents'
    )
    elevenlabs_default_voice_id = fields.Many2one(
        'voice.voice',
        string='Default Voice',
        help='Default voice for text-to-speech'
    )

    @api.model
    def get_values(self):
        """Override to populate API key from secure storage."""
        res = super().get_values()

        # Get API key from secure storage
        api_key = self.env['voice.token.storage'].get_secret('elevenlabs_api_key')
        if api_key:
            res['elevenlabs_api_key'] = api_key

        # Get default voice ID from parameter
        IrConfigParameter = self.env['ir.config_parameter'].sudo()
        voice_id = IrConfigParameter.get_param('voice_elevenlabs.default_voice_id')
        if voice_id:
            res['elevenlabs_default_voice_id'] = int(voice_id)

        return res

    def set_values(self):
        """Override to store API key securely."""
        super().set_values()

        # Store API key securely
        if self.elevenlabs_api_key:
            self.env['voice.token.storage'].store_secret(
                'elevenlabs_api_key',
                self.elevenlabs_api_key
            )

        # Store default voice ID
        IrConfigParameter = self.env['ir.config_parameter'].sudo()
        if self.elevenlabs_default_voice_id:
            IrConfigParameter.set_param(
                'voice_elevenlabs.default_voice_id',
                str(self.elevenlabs_default_voice_id.id)
            )
        else:
            IrConfigParameter.set_param('voice_elevenlabs.default_voice_id', '')

    def action_test_elevenlabs_connection(self):
        """Test the ElevenLabs API connection."""
        self.ensure_one()

        api_key = self.elevenlabs_api_key
        if not api_key:
            raise UserError(_('Please enter your ElevenLabs API key first.'))

        try:
            from elevenlabs import ElevenLabs
        except ImportError:
            raise UserError(_('ElevenLabs Python package not installed. Run: pip install elevenlabs'))

        try:
            client = ElevenLabs(api_key=api_key)

            # Test connection by fetching user info
            user_info = client.user.get()

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Connection Successful'),
                    'message': _('Connected to ElevenLabs. Subscription: %s') % (
                        getattr(user_info, 'subscription', {}).get('tier', 'Unknown')
                        if hasattr(user_info, 'subscription') else 'Unknown'
                    ),
                    'type': 'success',
                    'sticky': False,
                }
            }
        except Exception as e:
            raise UserError(_('Connection failed: %s') % str(e))

    def action_sync_elevenlabs_voices(self):
        """Sync voices from ElevenLabs."""
        self.ensure_one()

        api_key = self.elevenlabs_api_key
        if not api_key:
            raise UserError(_('Please configure your ElevenLabs API key first.'))

        try:
            from elevenlabs import ElevenLabs
        except ImportError:
            raise UserError(_('ElevenLabs Python package not installed.'))

        try:
            client = ElevenLabs(api_key=api_key)
            voices = client.voices.get_all()

            Voice = self.env['voice.voice']
            created_count = 0
            updated_count = 0

            for voice in voices.voices:
                voice_id = voice.voice_id
                existing = Voice.search([
                    ('external_voice_id', '=', voice_id),
                ], limit=1)

                vals = {
                    'name': voice.name,
                    'external_voice_id': voice_id,
                    'description': getattr(voice, 'description', '') or '',
                    'provider_type': 'elevenlabs',
                }

                # Extract gender and language from labels if available
                labels = getattr(voice, 'labels', {}) or {}
                if labels:
                    vals['gender'] = labels.get('gender', 'neutral')
                    vals['language'] = labels.get('language', 'en')

                if existing:
                    existing.write(vals)
                    updated_count += 1
                else:
                    Voice.create(vals)
                    created_count += 1

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Voice Sync Complete'),
                    'message': _('Created %d, Updated %d voices') % (created_count, updated_count),
                    'type': 'success',
                    'sticky': False,
                }
            }
        except Exception as e:
            raise UserError(_('Voice sync failed: %s') % str(e))
