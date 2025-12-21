# -*- coding: utf-8 -*-
import base64
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError


logger = logging.getLogger(__name__)


class VoiceProvider(models.AbstractModel):
    """
    Abstract interface for Voice AI providers.

    All provider implementations (voice_elevenlabs, voice_vapi, etc.) must inherit
    from this abstract model and implement the required abstract methods.

    Capability flags are computed by implementations to indicate which features
    are supported by each provider.
    """
    _name = 'voice.provider'
    _description = 'Voice AI Provider Interface'
    _order = 'sequence, name'

    # === Identity ===
    name = fields.Char(
        string='Name',
        required=True,
        help='Display name for this provider configuration'
    )
    sequence = fields.Integer(
        string='Sequence',
        default=10,
        help='Order in which providers are displayed'
    )
    provider_type = fields.Selection(
        selection=[],
        string='Provider',
        required=True,
        help='The voice AI provider (e.g., ElevenLabs, VAPI, Retell)'
    )
    active = fields.Boolean(
        string='Active',
        default=True,
        help='Whether this provider configuration is active'
    )
    is_default = fields.Boolean(
        string='Default Provider',
        help='Use this provider by default for new agents'
    )
    color = fields.Integer(
        string='Color',
        help='Color index for UI display'
    )

    # === Credentials ===
    api_key = fields.Char(
        string='API Key',
        groups='base.group_system',
        help='API key for authenticating with the provider'
    )
    api_endpoint = fields.Char(
        string='API Endpoint',
        help='Custom API endpoint URL (optional, uses provider default if not set)'
    )

    # === Capability Flags (computed by implementations) ===
    supports_tools = fields.Boolean(
        string='Supports Tools',
        compute='_compute_capabilities',
        store=False,
        help='Whether this provider supports custom tools/functions'
    )
    supports_mcp = fields.Boolean(
        string='Supports MCP',
        compute='_compute_capabilities',
        store=False,
        help='Whether this provider supports Model Context Protocol servers'
    )
    supports_knowledge_base = fields.Boolean(
        string='Supports Knowledge Base',
        compute='_compute_capabilities',
        store=False,
        help='Whether this provider supports knowledge base integration'
    )
    supports_voice_library = fields.Boolean(
        string='Supports Voice Library',
        compute='_compute_capabilities',
        store=False,
        help='Whether this provider has a voice library'
    )
    supports_voice_cloning = fields.Boolean(
        string='Supports Voice Cloning',
        compute='_compute_capabilities',
        store=False,
        help='Whether this provider supports voice cloning'
    )
    supports_telephony = fields.Boolean(
        string='Supports Telephony',
        compute='_compute_capabilities',
        store=False,
        help='Whether this provider supports phone calls'
    )
    supports_phone_registration = fields.Boolean(
        string='Supports Phone Registration',
        compute='_compute_capabilities',
        store=False,
        help='Whether this provider can register phone numbers'
    )
    supports_on_premise = fields.Boolean(
        string='Supports On-Premise',
        compute='_compute_capabilities',
        store=False,
        help='Whether this provider supports on-premise/self-hosted deployment'
    )

    # === Stats ===
    agent_count = fields.Integer(
        string='Agents',
        compute='_compute_stats',
        help='Number of agents using this provider'
    )
    conversation_count = fields.Integer(
        string='Conversations',
        compute='_compute_stats',
        help='Total conversations handled by this provider'
    )
    last_used = fields.Datetime(
        string='Last Used',
        compute='_compute_stats',
        help='When this provider was last used'
    )

    @api.depends('provider_type')
    def _compute_capabilities(self):
        """
        Compute capability flags based on provider type.

        Implementations should override this method to set their specific
        capabilities. Default implementation sets all flags to False.
        """
        for provider in self:
            provider.supports_tools = False
            provider.supports_mcp = False
            provider.supports_knowledge_base = False
            provider.supports_voice_library = False
            provider.supports_voice_cloning = False
            provider.supports_telephony = False
            provider.supports_phone_registration = False
            provider.supports_on_premise = False

    def _compute_stats(self):
        """Compute usage statistics for this provider."""
        for provider in self:
            # Default implementation - override in concrete models
            provider.agent_count = 0
            provider.conversation_count = 0
            provider.last_used = False

    @api.constrains('is_default')
    def _check_single_default(self):
        """Ensure only one provider can be marked as default."""
        for provider in self:
            if provider.is_default:
                other_defaults = self.search([
                    ('id', '!=', provider.id),
                    ('is_default', '=', True),
                    ('provider_type', '=', provider.provider_type),
                ])
                if other_defaults:
                    raise UserError(_(
                        'Only one provider can be marked as default. '
                        'Please uncheck the other default provider first.'
                    ))

    # === Abstract Methods (must be implemented by provider modules) ===

    def get_client(self):
        """
        Return provider-specific API client instance.

        Returns:
            object: Provider-specific client object
        """
        raise NotImplementedError(_('Method get_client() must be implemented by provider'))

    def get_conversation_handler(self):
        """
        Return conversation handler instance for this provider.

        Returns:
            object: Provider-specific conversation handler
        """
        raise NotImplementedError(_('Method get_conversation_handler() must be implemented by provider'))

    def text_to_speech(self, text, voice_id, **kwargs):
        """
        Convert text to speech audio.

        Args:
            text (str): Text to convert to speech
            voice_id (str): Voice ID to use
            **kwargs: Provider-specific options (model, stability, etc.)

        Returns:
            bytes: Audio data
        """
        raise NotImplementedError(_('Method text_to_speech() must be implemented by provider'))

    def speech_to_text(self, audio_bytes, **kwargs):
        """
        Convert speech audio to text.

        Args:
            audio_bytes (bytes): Audio data to transcribe
            **kwargs: Provider-specific options (language, etc.)

        Returns:
            str: Transcribed text
        """
        raise NotImplementedError(_('Method speech_to_text() must be implemented by provider'))

    def sync_voices(self):
        """
        Sync available voices from provider to voice.voice model.

        Returns:
            dict: {'synced': int, 'created': int, 'updated': int}
        """
        raise NotImplementedError(_('Method sync_voices() must be implemented by provider'))

    def get_audio_config(self):
        """
        Return audio format configuration for this provider.

        Returns:
            dict: {'format': str, 'sample_rate': int, 'channels': int, 'encoding': str}
        """
        raise NotImplementedError(_('Method get_audio_config() must be implemented by provider'))

    def test_connection(self):
        """
        Test connection to provider API.

        Returns:
            dict: {'success': bool, 'message': str, 'details': dict}
        """
        raise NotImplementedError(_('Method test_connection() must be implemented by provider'))

    # === Helper Methods ===

    def action_test_connection(self):
        """Action to test provider connection from UI."""
        self.ensure_one()
        result = self.test_connection()

        if result.get('success'):
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Connection Successful'),
                    'message': result.get('message', _('Successfully connected to provider')),
                    'type': 'success',
                    'sticky': False,
                }
            }
        else:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Connection Failed'),
                    'message': result.get('message', _('Failed to connect to provider')),
                    'type': 'danger',
                    'sticky': True,
                }
            }

    def action_sync_voices(self):
        """Action to sync voices from provider."""
        self.ensure_one()
        result = self.sync_voices()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Voice Sync Complete'),
                'message': _(
                    'Synced %(synced)d voices (%(created)d created, %(updated)d updated)',
                    synced=result.get('synced', 0),
                    created=result.get('created', 0),
                    updated=result.get('updated', 0),
                ),
                'type': 'success',
                'sticky': False,
            }
        }

    # === TTS Helper Methods ===

    def generate_tts(self, text, voice_id=None, model_id=None, **kwargs):
        """
        Generate TTS audio and return as base64-encoded string.

        This is a convenience wrapper around text_to_speech() that handles
        the encoding needed for Binary fields in Odoo.

        Args:
            text (str): Text to convert to speech
            voice_id (str): Optional voice ID (uses default if not set)
            model_id (str): Optional TTS model ID
            **kwargs: Provider-specific options

        Returns:
            str: Base64-encoded audio data, or None if generation fails
        """
        self.ensure_one()
        try:
            audio_bytes = self.text_to_speech(text, voice_id, model_id=model_id, **kwargs)
            if audio_bytes:
                return base64.b64encode(audio_bytes).decode('utf-8')
        except NotImplementedError:
            logger.debug('TTS not implemented for provider %s', self.name)
        except Exception as e:
            logger.error('TTS generation failed for provider %s: %s', self.name, e)
        return None

    # === Migration Detection Utilities ===

    @api.model
    def is_legacy_module_installed(self, module_name):
        """
        Check if a legacy module is installed.

        Args:
            module_name (str): Module technical name (e.g., 'connect_elevenlabs')

        Returns:
            bool: True if module is installed
        """
        module = self.env['ir.module.module'].sudo().search([
            ('name', '=', module_name),
            ('state', '=', 'installed'),
        ], limit=1)
        return bool(module)

    @api.model
    def get_legacy_module_version(self, module_name):
        """
        Get installed version of a legacy module.

        Args:
            module_name (str): Module technical name

        Returns:
            str: Installed version or None if not installed
        """
        module = self.env['ir.module.module'].sudo().search([
            ('name', '=', module_name),
            ('state', '=', 'installed'),
        ], limit=1)
        return module.installed_version if module else None

    @api.model
    def detect_legacy_installation(self):
        """
        Detect all installed legacy voice modules.

        Returns:
            dict: {module_name: version} for all detected legacy modules
        """
        legacy_modules = [
            'connect_elevenlabs',
            'connect_elevenlabs_sale',
            'connect_elevenlabs_callout',
        ]
        detected = {}
        for module_name in legacy_modules:
            version = self.get_legacy_module_version(module_name)
            if version:
                detected[module_name] = version
        return detected

    @api.model
    def get_legacy_record_count(self, model_name):
        """
        Get count of records in a legacy model.

        Args:
            model_name (str): Model name (e.g., 'connect.elevenlabs_agent')

        Returns:
            int: Record count, or 0 if model doesn't exist
        """
        try:
            Model = self.env[model_name]
            return Model.sudo().search_count([])
        except KeyError:
            return 0
        except Exception as e:
            logger.warning('Could not count records in %s: %s', model_name, e)
            return 0

    @api.model
    def get_migration_summary(self):
        """
        Get a complete migration summary for legacy voice modules.

        Returns:
            dict: {
                'legacy_installed': {module: version, ...},
                'legacy_models': {model: count, ...},
                'migration_required': bool,
            }
        """
        legacy_modules = self.detect_legacy_installation()

        legacy_models = {}
        if 'connect_elevenlabs' in legacy_modules:
            model_names = [
                'connect.elevenlabs_agent',
                'connect.elevenlabs_voice',
                'connect.elevenlabs_file',
                'connect.elevenlabs_system_message',
                'connect.elevenlabs_agent_tool',
                'connect.agent_tool_params',
                'connect.elevenlabs_phone_registration',
            ]
            for model_name in model_names:
                count = self.get_legacy_record_count(model_name)
                if count > 0:
                    legacy_models[model_name] = count

        return {
            'legacy_installed': legacy_modules,
            'legacy_models': legacy_models,
            'migration_required': bool(legacy_modules),
        }
