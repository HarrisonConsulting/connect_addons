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

        Returns:
            ElevenLabsConversationHandler: Configured handler instance
        """
        self.ensure_one()
        from .conversation_handler import ElevenLabsConversationHandler
        return ElevenLabsConversationHandler(self)

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

    # === Agent Management ===

    def create_agent(self, config):
        """
        Create a new agent in ElevenLabs.

        Args:
            config (dict): Agent configuration (from ElevenLabsAgentConfig.build())

        Returns:
            dict: {'agent_id': str, 'success': bool, 'details': dict}
        """
        self.ensure_one()
        client = self.get_client()

        try:
            response = client.conversational_ai.agents.create(
                conversation_config=config.get('conversation_config', {}),
                platform_settings=config.get('platform_settings'),
                name=config.get('name'),
            )

            agent_id = getattr(response, 'agent_id', None)
            if not agent_id:
                raise UserError(_('No agent_id in response'))

            logger.info('Created ElevenLabs agent: %s', agent_id)
            return {
                'agent_id': agent_id,
                'success': True,
                'details': self._parse_agent_response(response),
            }

        except Exception as e:
            logger.error('Failed to create agent: %s', e)
            raise UserError(_('Failed to create agent: %s') % str(e))

    def update_agent(self, agent_id, config):
        """
        Update an existing agent in ElevenLabs.

        Args:
            agent_id (str): ElevenLabs agent ID
            config (dict): Agent configuration

        Returns:
            dict: {'success': bool, 'details': dict}
        """
        self.ensure_one()
        client = self.get_client()

        try:
            response = client.conversational_ai.agents.update(
                agent_id=agent_id,
                conversation_config=config.get('conversation_config'),
                platform_settings=config.get('platform_settings'),
                name=config.get('name'),
            )

            logger.info('Updated ElevenLabs agent: %s', agent_id)
            return {
                'success': True,
                'details': self._parse_agent_response(response),
            }

        except Exception as e:
            logger.error('Failed to update agent %s: %s', agent_id, e)
            raise UserError(_('Failed to update agent: %s') % str(e))

    def get_agent(self, agent_id):
        """
        Get agent details from ElevenLabs.

        Args:
            agent_id (str): ElevenLabs agent ID

        Returns:
            dict: Agent details
        """
        self.ensure_one()
        client = self.get_client()

        try:
            response = client.conversational_ai.agents.get(agent_id=agent_id)
            return self._parse_agent_response(response)

        except Exception as e:
            logger.error('Failed to get agent %s: %s', agent_id, e)
            raise UserError(_('Failed to get agent: %s') % str(e))

    def delete_agent(self, agent_id):
        """
        Delete an agent from ElevenLabs.

        Args:
            agent_id (str): ElevenLabs agent ID

        Returns:
            dict: {'success': bool}
        """
        self.ensure_one()
        client = self.get_client()

        try:
            client.conversational_ai.agents.delete(agent_id=agent_id)
            logger.info('Deleted ElevenLabs agent: %s', agent_id)
            return {'success': True}

        except Exception as e:
            logger.error('Failed to delete agent %s: %s', agent_id, e)
            raise UserError(_('Failed to delete agent: %s') % str(e))

    def list_agents(self):
        """
        List all agents from ElevenLabs account.

        Returns:
            list: List of agent dicts
        """
        self.ensure_one()
        client = self.get_client()

        try:
            response = client.conversational_ai.agents.list()
            agents = getattr(response, 'agents', [])
            return [self._parse_agent_response(a) for a in agents]

        except Exception as e:
            logger.error('Failed to list agents: %s', e)
            raise UserError(_('Failed to list agents: %s') % str(e))

    def _parse_agent_response(self, response):
        """Parse ElevenLabs agent response to dictionary."""
        if response is None:
            return {}

        result = {}
        for attr in ['agent_id', 'name', 'conversation_config',
                     'platform_settings', 'metadata', 'secrets']:
            if hasattr(response, attr):
                value = getattr(response, attr)
                # Convert nested objects to dicts
                if hasattr(value, '__dict__'):
                    result[attr] = self._object_to_dict(value)
                else:
                    result[attr] = value
        return result

    def _object_to_dict(self, obj):
        """Recursively convert object to dictionary."""
        if obj is None:
            return None
        if isinstance(obj, (str, int, float, bool)):
            return obj
        if isinstance(obj, (list, tuple)):
            return [self._object_to_dict(i) for i in obj]
        if isinstance(obj, dict):
            return {k: self._object_to_dict(v) for k, v in obj.items()}
        if hasattr(obj, '__dict__'):
            return {k: self._object_to_dict(v)
                    for k, v in obj.__dict__.items()
                    if not k.startswith('_')}
        return str(obj)

    # === Phone Number Management ===

    def register_phone_number(self, phone_number, agent_id, label=None):
        """
        Register a phone number with an ElevenLabs agent.

        Args:
            phone_number (str): Phone number in E.164 format
            agent_id (str): ElevenLabs agent ID
            label (str): Optional label for the phone number

        Returns:
            dict: {'success': bool, 'phone_number_id': str}
        """
        self.ensure_one()
        client = self.get_client()

        try:
            response = client.conversational_ai.phone_numbers.create(
                phone_number=phone_number,
                agent_id=agent_id,
                label=label,
            )

            phone_number_id = getattr(response, 'phone_number_id', None)
            logger.info('Registered phone number %s with agent %s',
                       phone_number, agent_id)

            return {
                'success': True,
                'phone_number_id': phone_number_id,
            }

        except Exception as e:
            logger.error('Failed to register phone number: %s', e)
            raise UserError(_('Failed to register phone number: %s') % str(e))

    def unregister_phone_number(self, phone_number_id):
        """
        Unregister a phone number from ElevenLabs.

        Args:
            phone_number_id (str): ElevenLabs phone number ID

        Returns:
            dict: {'success': bool}
        """
        self.ensure_one()
        client = self.get_client()

        try:
            client.conversational_ai.phone_numbers.delete(
                phone_number_id=phone_number_id
            )
            logger.info('Unregistered phone number: %s', phone_number_id)
            return {'success': True}

        except Exception as e:
            logger.error('Failed to unregister phone number: %s', e)
            raise UserError(_('Failed to unregister phone number: %s') % str(e))

    # === Outbound Calling ===

    def initiate_outbound_call(self, agent_id, to_number, dynamic_variables=None,
                               first_message=None, max_duration=None,
                               phone_number_id=None):
        """
        Initiate an outbound call using ElevenLabs Conversational AI.

        Uses the ElevenLabs Twilio outbound call API to start a call
        where an AI agent handles the conversation.

        Args:
            agent_id (str): ElevenLabs agent ID
            to_number (str): Phone number to call (E.164 format)
            dynamic_variables (dict): Variables for prompt personalization
            first_message (str): Override agent's default first message
            max_duration (int): Maximum call duration in seconds
            phone_number_id (str): ElevenLabs phone number ID for caller ID

        Returns:
            dict: {'call_sid': str, 'success': bool, 'details': dict}
        """
        self.ensure_one()
        client = self.get_client()

        # Get Twilio credentials from Connect settings
        try:
            Settings = self.env['connect.settings'].sudo()
            twilio_account_sid = Settings.get_param('twilio_sid')
            twilio_auth_token = Settings.get_param('twilio_token')

            if not twilio_account_sid or not twilio_auth_token:
                raise UserError(_('Twilio credentials not configured in Connect settings.'))

        except Exception as e:
            raise UserError(_('Failed to get Twilio credentials: %s') % str(e))

        # Find registered phone number if not provided
        if not phone_number_id:
            phone_reg = self.env['elevenlabs.phone.registration'].search([
                ('provider_id', '=', self.id),
                ('sync_status', '=', 'synced'),
            ], limit=1)
            if phone_reg:
                phone_number_id = phone_reg.phone_number_id
            else:
                raise UserError(_(
                    'No registered phone number found for outbound calling. '
                    'Please register a phone number with ElevenLabs first.'
                ))

        try:
            # Build conversation config overrides
            conversation_config_override = {}

            if first_message:
                conversation_config_override['agent'] = {
                    'first_message': first_message,
                }

            if max_duration:
                conversation_config_override['conversation'] = {
                    'max_duration_seconds': max_duration,
                }

            # Call ElevenLabs Twilio outbound API
            response = client.conversational_ai.twilio.initiate_outbound_call(
                agent_id=agent_id,
                agent_phone_number_id=phone_number_id,
                to_phone_number=to_number,
                twilio_account_sid=twilio_account_sid,
                twilio_auth_token=twilio_auth_token,
                conversation_config_override=conversation_config_override if conversation_config_override else None,
                dynamic_variables=dynamic_variables if dynamic_variables else None,
            )

            # Extract call SID from response
            call_sid = getattr(response, 'call_sid', None) or getattr(response, 'twilio_call_sid', None)

            logger.info('Initiated outbound call to %s, call_sid=%s', to_number, call_sid)

            return {
                'call_sid': call_sid,
                'success': True,
                'details': self._object_to_dict(response) if response else {},
            }

        except Exception as e:
            logger.error('Failed to initiate outbound call: %s', e)
            raise UserError(_('Failed to initiate outbound call: %s') % str(e))

    # === Agent Sync ===

    def sync_agent(self, agent_record):
        """
        Sync an agent configuration to ElevenLabs.

        Takes a record that uses voice.agent.mixin and creates/updates
        the corresponding agent in ElevenLabs.

        Args:
            agent_record: Record implementing voice.agent.mixin

        Returns:
            dict: {'success': bool, 'agent_id': str, 'action': 'created'|'updated'}
        """
        self.ensure_one()

        # Build agent configuration from mixin fields
        config = self._build_agent_config_from_mixin(agent_record)

        try:
            if agent_record.external_agent_id:
                # Update existing agent
                result = self.update_agent(agent_record.external_agent_id, config)
                agent_record.write({
                    'sync_status': 'synced',
                    'sync_error': False,
                    'last_sync': fields.Datetime.now(),
                })
                return {
                    'success': True,
                    'agent_id': agent_record.external_agent_id,
                    'action': 'updated',
                }
            else:
                # Create new agent
                result = self.create_agent(config)
                agent_record.write({
                    'external_agent_id': result['agent_id'],
                    'sync_status': 'synced',
                    'sync_error': False,
                    'last_sync': fields.Datetime.now(),
                })
                return {
                    'success': True,
                    'agent_id': result['agent_id'],
                    'action': 'created',
                }

        except Exception as e:
            agent_record.write({
                'sync_status': 'error',
                'sync_error': str(e),
            })
            raise

    def _build_agent_config_from_mixin(self, agent_record):
        """
        Build ElevenLabs agent configuration from voice.agent.mixin fields.

        Args:
            agent_record: Record implementing voice.agent.mixin

        Returns:
            dict: Agent configuration for ElevenLabs API
        """
        from .conversation_handler import ElevenLabsAgentConfig

        config = ElevenLabsAgentConfig()

        # Set name
        config.set_name(agent_record.name if hasattr(agent_record, 'name') else 'Agent')

        # Set prompt and first message
        config.set_prompt(
            system_prompt=agent_record.system_prompt or '',
            first_message=agent_record.first_message,
        )

        # Set LLM configuration
        config.set_llm(
            model=agent_record.llm_model or 'gpt-4o',
            temperature=agent_record.temperature or 1.0,
            max_tokens=agent_record.max_tokens or 500,
        )

        # Set voice if available
        if agent_record.voice_id and agent_record.voice_id.external_voice_id:
            config.set_voice(
                voice_id=agent_record.voice_id.external_voice_id,
                stability=agent_record.stability or 0.5,
                similarity_boost=agent_record.similarity_boost or 0.8,
                model_id=agent_record.tts_model or 'eleven_flash_v2_5',
            )

        # Set language
        if agent_record.language:
            config.set_language(agent_record.language)

        # Set audio format for telephony
        config.set_audio_format(output_format='ulaw_8000', sample_rate=8000)

        # Add tools if configured
        if agent_record.tool_ids:
            for tool in agent_record.tool_ids:
                tool_config = self._build_tool_config(tool)
                if tool_config:
                    config.add_tool(tool_config)

        return config.build()

    def _build_tool_config(self, tool):
        """
        Build tool configuration for ElevenLabs.

        Args:
            tool: voice.tool record

        Returns:
            dict: Tool configuration or None
        """
        if tool.tool_type == 'webhook':
            return {
                'type': 'webhook',
                'name': tool.name,
                'description': tool.description or '',
                'api_schema': {
                    'url': tool.webhook_url,
                    'method': tool.http_method or 'POST',
                },
            }
        elif tool.tool_type == 'client':
            return {
                'type': 'client',
                'name': tool.name,
                'description': tool.description or '',
            }
        elif tool.tool_type == 'system':
            return {
                'type': 'system',
                'name': tool.name,
                'description': tool.description or '',
                'system_tool_type': tool.system_tool_type,
            }
        return None

    # === Inbound Call Rendering ===

    def render_agent_response(self, agent_id, call_id, stream_url):
        """
        Generate TwiML response for routing a call to a voice agent.

        Creates a VoiceResponse that connects Twilio to the ElevenLabs
        WebSocket stream for agent handling.

        Args:
            agent_id (str): ElevenLabs agent ID
            call_id (int): Connect call ID
            stream_url (str): WebSocket URL for audio streaming

        Returns:
            str: TwiML response XML
        """
        try:
            from twilio.twiml.voice_response import VoiceResponse, Connect
        except ImportError:
            raise UserError(_('Twilio SDK not installed. Install with: pip install twilio'))

        response = VoiceResponse()
        connect = Connect()

        # Build stream URL with agent and call info
        full_stream_url = f"{stream_url}/{agent_id}/{call_id}"

        connect.stream(url=full_stream_url)
        response.append(connect)

        return str(response)

    # === Conversation Audio ===

    def fetch_conversation_audio(self, conversation_id):
        """
        Fetch audio recording for a conversation from ElevenLabs.

        Args:
            conversation_id (str): ElevenLabs conversation ID

        Returns:
            bytes: Audio data (MP3 format) or None if not available
        """
        self.ensure_one()

        if not self.api_key:
            logger.error('No API key configured for provider %s', self.name)
            return None

        try:
            import requests

            url = f"https://api.elevenlabs.io/v1/convai/conversations/{conversation_id}/audio"
            headers = {
                "Content-Type": "application/json",
                "xi-api-key": self.api_key,
            }

            response = requests.get(url, headers=headers, timeout=30)

            if response.status_code == 200:
                logger.info('Fetched audio for conversation %s (%d bytes)',
                           conversation_id, len(response.content))
                return response.content
            else:
                logger.warning('Failed to fetch audio for conversation %s: HTTP %d',
                              conversation_id, response.status_code)
                return None

        except Exception as e:
            logger.error('Error fetching conversation audio: %s', e)
            return None

    def get_conversation_audio_url(self, conversation_id):
        """
        Get the audio URL for a conversation (for direct streaming).

        Note: This URL requires authentication, so it's typically better
        to use fetch_conversation_audio and serve via Odoo.

        Args:
            conversation_id (str): ElevenLabs conversation ID

        Returns:
            str: Audio URL
        """
        return f"https://api.elevenlabs.io/v1/convai/conversations/{conversation_id}/audio"
