# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

logger = logging.getLogger(__name__)

# Import ElevenLabs SDK
try:
    from elevenlabs import ElevenLabs
except ImportError:
    logger.warning("elevenlabs package not installed. Install with: pip install elevenlabs")
    ElevenLabs = None


class VoiceProviderElevenLabs(models.Model):
    """
    ElevenLabs implementation of the voice.provider interface.

    Provides full integration with ElevenLabs Conversational AI including:
    - Voice library sync (5000+ voices)
    - Tools (webhooks, client, system)
    - MCP server integration
    - Knowledge base
    - Twilio telephony
    - Phone registration
    """
    _name = 'voice.provider.elevenlabs'
    _inherit = 'voice.provider'
    _description = 'ElevenLabs Voice Provider'

    # Override provider_type selection to add elevenlabs
    provider_type = fields.Selection(
        selection_add=[('elevenlabs', 'ElevenLabs')],
        ondelete={'elevenlabs': 'cascade'},
    )

    # === ElevenLabs Specific Configuration ===
    webhook_secret = fields.Char(
        string='Webhook Secret',
        groups='base.group_system',
        help='Secret key for validating webhook signatures from ElevenLabs'
    )

    # === Default Settings ===
    default_tts_model = fields.Selection(
        selection=[
            ('eleven_flash_v2_5', 'Flash v2.5 (Fastest)'),
            ('eleven_flash_v2', 'Flash v2'),
            ('eleven_turbo_v2_5', 'Turbo v2.5'),
            ('eleven_turbo_v2', 'Turbo v2'),
            ('eleven_multilingual_v2', 'Multilingual v2'),
        ],
        string='Default TTS Model',
        default='eleven_flash_v2_5',
        help='Default text-to-speech model for this provider'
    )
    default_llm_model = fields.Selection(
        selection=[
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
        ],
        string='Default LLM Model',
        default='gpt-4o',
        help='Default language model for this provider'
    )

    @api.depends('provider_type')
    def _compute_capabilities(self):
        """Set ElevenLabs capabilities."""
        super()._compute_capabilities()
        for provider in self:
            if provider.provider_type == 'elevenlabs':
                provider.supports_tools = True
                provider.supports_mcp = True
                provider.supports_knowledge_base = True
                provider.supports_voice_library = True
                provider.supports_voice_cloning = True
                provider.supports_telephony = True
                provider.supports_phone_registration = True
                provider.supports_on_premise = False

    def _compute_stats(self):
        """Compute usage statistics for ElevenLabs provider."""
        super()._compute_stats()
        for provider in self:
            if provider.provider_type == 'elevenlabs':
                # Count phone registrations as proxy for agents
                phone_count = self.env['elevenlabs.phone.registration'].search_count([
                    ('provider_id', '=', provider.id)
                ])
                provider.agent_count = phone_count

                # Count conversations handled by this provider
                provider.conversation_count = self.env['voice.conversation'].search_count([
                    ('voice_provider_id', '=', provider.id)
                ])

                # Get last used timestamp
                last_conversation = self.env['voice.conversation'].search([
                    ('voice_provider_id', '=', provider.id)
                ], order='create_date desc', limit=1)
                provider.last_used = last_conversation.create_date if last_conversation else False

    def get_client(self):
        """
        Return ElevenLabs SDK client instance.

        Returns:
            ElevenLabs: Configured ElevenLabs client
        """
        self.ensure_one()

        if not ElevenLabs:
            raise UserError(_(
                'ElevenLabs SDK not installed. '
                'Please install it with: pip install elevenlabs'
            ))

        if not self.api_key:
            raise UserError(_(
                'API key not configured for provider "%s". '
                'Please set the API key in the provider settings.'
            ) % self.name)

        try:
            client = ElevenLabs(api_key=self.api_key)

            # Set custom endpoint if configured
            if self.api_endpoint:
                client.base_url = self.api_endpoint

            return client
        except Exception as e:
            raise UserError(_(
                'Failed to initialize ElevenLabs client: %s'
            ) % str(e))

    def get_conversation_handler(self):
        """
        Return conversation handler for ElevenLabs.

        This would be implemented when the conversation handling service
        is abstracted from connect_elevenlabs.
        """
        self.ensure_one()
        raise NotImplementedError(_(
            'Conversation handler not yet implemented for ElevenLabs provider'
        ))

    def text_to_speech(self, text, voice_id, **kwargs):
        """
        Convert text to speech using ElevenLabs.

        Args:
            text (str): Text to convert
            voice_id (str): ElevenLabs voice ID
            **kwargs: Additional options (model_id, voice_settings, etc.)

        Returns:
            bytes: Audio data (MP3)
        """
        self.ensure_one()
        client = self.get_client()

        try:
            model_id = kwargs.get('model_id', self.default_tts_model or 'eleven_flash_v2_5')
            voice_settings = kwargs.get('voice_settings')

            # Call ElevenLabs TTS API
            audio_generator = client.text_to_speech.convert(
                voice_id=voice_id,
                text=text,
                model_id=model_id,
                voice_settings=voice_settings,
            )

            # Collect audio bytes from generator
            audio_bytes = b''.join(audio_generator)
            return audio_bytes

        except Exception as e:
            logger.error("ElevenLabs TTS error: %s", e)
            raise UserError(_('Text-to-speech conversion failed: %s') % str(e))

    def speech_to_text(self, audio_bytes, **kwargs):
        """
        Convert speech to text using ElevenLabs.

        Args:
            audio_bytes (bytes): Audio data to transcribe
            **kwargs: Additional options (language, etc.)

        Returns:
            str: Transcribed text
        """
        self.ensure_one()
        client = self.get_client()

        try:
            # ElevenLabs speech-to-text API
            result = client.speech_to_text.convert(
                audio=audio_bytes,
                model_id=kwargs.get('model_id', 'scribe_v1'),
            )

            return result.text if hasattr(result, 'text') else str(result)

        except Exception as e:
            logger.error("ElevenLabs STT error: %s", e)
            raise UserError(_('Speech-to-text conversion failed: %s') % str(e))

    def sync_voices(self):
        """
        Sync available voices from ElevenLabs to voice.voice model.

        Returns:
            dict: {'synced': int, 'created': int, 'updated': int}
        """
        self.ensure_one()
        client = self.get_client()

        try:
            # Fetch all voices from ElevenLabs
            response = client.voices.get_all()
            voices = response.voices if hasattr(response, 'voices') else []

            created = 0
            updated = 0

            for el_voice in voices:
                # Extract voice data
                voice_data = {
                    'name': el_voice.name,
                    'voice_provider_id': self.id,
                    'external_voice_id': el_voice.voice_id,
                    'preview_url': getattr(el_voice, 'preview_url', False),
                    'description': el_voice.labels.get('description') if hasattr(el_voice, 'labels') else False,
                }

                # Extract labels if available
                if hasattr(el_voice, 'labels'):
                    labels = el_voice.labels
                    if isinstance(labels, dict):
                        # Map gender
                        gender_map = {'male': 'male', 'female': 'female', 'neutral': 'neutral'}
                        gender = labels.get('gender', '').lower()
                        if gender in gender_map:
                            voice_data['gender'] = gender_map[gender]

                        # Map age
                        age_map = {'young': 'young', 'middle aged': 'middle_aged', 'old': 'old'}
                        age = labels.get('age', '').lower()
                        if age in age_map:
                            voice_data['age'] = age_map[age]

                        # Set accent
                        if labels.get('accent'):
                            voice_data['accent'] = labels['accent']

                # Extract language if available
                if hasattr(el_voice, 'fine_tuning') and hasattr(el_voice.fine_tuning, 'language'):
                    voice_data['language_code'] = el_voice.fine_tuning.language

                # Find or create voice
                existing_voice = self.env['voice.voice'].search([
                    ('voice_provider_id', '=', self.id),
                    ('external_voice_id', '=', el_voice.voice_id),
                ], limit=1)

                if existing_voice:
                    existing_voice.write(voice_data)
                    updated += 1
                else:
                    self.env['voice.voice'].create(voice_data)
                    created += 1

            synced = created + updated

            logger.info(
                "ElevenLabs voice sync complete: %d synced (%d created, %d updated)",
                synced, created, updated
            )

            return {
                'synced': synced,
                'created': created,
                'updated': updated,
            }

        except Exception as e:
            logger.error("Voice sync error: %s", e)
            raise UserError(_('Voice synchronization failed: %s') % str(e))

    def get_audio_config(self):
        """
        Return audio format configuration for ElevenLabs.

        Returns:
            dict: Audio configuration
        """
        self.ensure_one()
        return {
            'format': 'ulaw_8000',  # Default for telephony
            'sample_rate': 8000,
            'channels': 1,
            'encoding': 'ulaw',
        }

    def test_connection(self):
        """
        Test connection to ElevenLabs API.

        Returns:
            dict: {'success': bool, 'message': str, 'details': dict}
        """
        self.ensure_one()

        try:
            client = self.get_client()

            # Test API by fetching user info
            response = client.user.get()

            # Extract user details if available
            details = {}
            if hasattr(response, 'subscription'):
                subscription = response.subscription
                details['subscription_tier'] = getattr(subscription, 'tier', 'Unknown')
                details['character_count'] = getattr(subscription, 'character_count', 0)
                details['character_limit'] = getattr(subscription, 'character_limit', 0)

            return {
                'success': True,
                'message': _('Successfully connected to ElevenLabs API'),
                'details': details,
            }

        except Exception as e:
            logger.error("Connection test failed: %s", e)
            return {
                'success': False,
                'message': _('Connection failed: %s') % str(e),
                'details': {'error': str(e)},
            }
