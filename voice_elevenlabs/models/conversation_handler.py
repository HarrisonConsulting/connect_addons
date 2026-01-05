# -*- coding: utf-8 -*-
"""
ElevenLabs Conversation Handler.

This module provides the conversation management layer for ElevenLabs
Conversational AI integration. It wraps the ElevenLabs SDK Conversation
class and provides a clean interface for the voice provider abstraction.
"""

import logging
from odoo import _
from odoo.exceptions import UserError

logger = logging.getLogger(__name__)

# Import ElevenLabs SDK
try:
    from elevenlabs.conversational_ai.conversation import (
        ClientTools,
        Conversation,
        ConversationInitiationData,
    )
    HAS_ELEVENLABS_SDK = True
except ImportError:
    logger.warning("ElevenLabs conversational AI SDK not available")
    ClientTools = None
    Conversation = None
    ConversationInitiationData = None
    HAS_ELEVENLABS_SDK = False


class ElevenLabsConversationHandler:
    """
    Handler for ElevenLabs Conversational AI sessions.

    This class wraps the ElevenLabs Conversation SDK and provides
    a clean interface for managing conversation sessions.

    Usage:
        provider = env['voice.provider.elevenlabs'].browse(provider_id)
        handler = provider.get_conversation_handler()
        handler.configure(agent_id='xxx', dynamic_variables={...})
        handler.start()
        # ... handle audio ...
        handler.end()
    """

    def __init__(self, provider):
        """
        Initialize the conversation handler.

        Args:
            provider: voice.provider.elevenlabs record
        """
        if not HAS_ELEVENLABS_SDK:
            raise UserError(_(
                'ElevenLabs Conversational AI SDK not installed. '
                'Install with: pip install elevenlabs[conversational]'
            ))

        self.provider = provider
        self.client = provider.get_client()
        self.agent_id = None
        self.conversation = None
        self.client_tools = ClientTools() if ClientTools else None
        self.audio_interface = None
        self.config = None
        self._callbacks = {}

    def configure(self, agent_id, dynamic_variables=None, language='en',
                  audio_interface=None, **kwargs):
        """
        Configure the conversation session.

        Args:
            agent_id (str): ElevenLabs agent ID
            dynamic_variables (dict): Variables for prompt interpolation
            language (str): Conversation language code
            audio_interface: Audio interface implementation
            **kwargs: Additional configuration options
        """
        self.agent_id = agent_id
        self.audio_interface = audio_interface

        # Build conversation config
        config_override = {
            'agent': {'language': language}
        }

        # Allow additional overrides
        if 'conversation_config_override' in kwargs:
            config_override.update(kwargs['conversation_config_override'])

        self.config = ConversationInitiationData(
            dynamic_variables=dynamic_variables or {},
            conversation_config_override=config_override,
        )

        return self

    def register_tool(self, name, handler, is_async=True):
        """
        Register a client-side tool for the conversation.

        Args:
            name (str): Tool name (must match agent tool configuration)
            handler: Async callable that handles tool invocation
            is_async (bool): Whether handler is async
        """
        if self.client_tools:
            self.client_tools.register(name, handler, is_async=is_async)
        return self

    def set_callback(self, event, callback):
        """
        Set callback for conversation events.

        Args:
            event (str): Event name (agent_response, user_transcript, etc.)
            callback: Callable to invoke on event
        """
        self._callbacks[event] = callback
        return self

    def _get_callback(self, event):
        """Get callback for event or return no-op."""
        return self._callbacks.get(event, lambda *args: None)

    def start(self):
        """
        Start the conversation session.

        Returns:
            self for method chaining
        """
        if not self.agent_id:
            raise UserError(_('Agent ID not configured. Call configure() first.'))

        try:
            self.conversation = Conversation(
                client=self.client,
                agent_id=self.agent_id,
                config=self.config,
                requires_auth=False,
                client_tools=self.client_tools,
                audio_interface=self.audio_interface,
                callback_agent_response=self._get_callback('agent_response'),
                callback_agent_response_correction=self._get_callback('agent_response_correction'),
                callback_user_transcript=self._get_callback('user_transcript'),
            )

            self.conversation.start_session()
            logger.info('ElevenLabs conversation started for agent %s', self.agent_id)
            return self

        except Exception as e:
            logger.error('Failed to start conversation: %s', e)
            raise UserError(_('Failed to start conversation: %s') % str(e))

    def end(self):
        """
        End the conversation session.

        Returns:
            str: Conversation ID from ElevenLabs
        """
        if not self.conversation:
            return None

        try:
            self.conversation.end_session()
            conversation_id = self.conversation.wait_for_session_end()
            logger.info('Conversation ended: %s', conversation_id)
            return conversation_id
        except Exception as e:
            logger.error('Error ending conversation: %s', e)
            return None

    @property
    def is_active(self):
        """Check if conversation is currently active."""
        return self.conversation is not None

    def get_conversation_id(self):
        """Get the current conversation ID if available."""
        if self.conversation and hasattr(self.conversation, 'conversation_id'):
            return self.conversation.conversation_id
        return None


class ElevenLabsAgentConfig:
    """
    Helper class for building ElevenLabs agent configuration.

    This matches the structure expected by the ElevenLabs API for
    creating/updating agents.
    """

    def __init__(self):
        self.config = {
            'conversation_config': {
                'agent': {},
                'tts': {},
                'stt': {},
            },
            'platform_settings': {},
        }

    def set_name(self, name):
        """Set agent name."""
        self.config['name'] = name
        return self

    def set_prompt(self, system_prompt, first_message=None):
        """Set agent prompts."""
        agent_config = self.config['conversation_config']['agent']
        agent_config['prompt'] = {
            'prompt': system_prompt,
        }
        if first_message:
            agent_config['first_message'] = first_message
        return self

    def set_llm(self, model, temperature=1.0, max_tokens=None):
        """Set LLM configuration."""
        agent_config = self.config['conversation_config']['agent']
        agent_config['llm'] = {
            'model': model,
            'temperature': temperature,
        }
        if max_tokens:
            agent_config['llm']['max_tokens'] = max_tokens
        return self

    def set_custom_llm(self, url, model_id=None, api_key=None, extra_body=None):
        """
        Set custom LLM configuration for OpenAI-compatible endpoints.

        Args:
            url (str): Chat completions endpoint URL
            model_id (str): Model identifier
            api_key (str): API key for authentication
            extra_body (dict): Additional request parameters
        """
        agent_config = self.config['conversation_config']['agent']

        # Set base LLM model to custom-llm
        agent_config['llm'] = {
            'model': 'custom-llm',
        }

        # Add custom_llm configuration
        custom_llm = {'url': url}
        if model_id:
            custom_llm['model_id'] = model_id
        if api_key:
            custom_llm['api_key'] = api_key
        if extra_body:
            custom_llm['extra_body'] = extra_body

        agent_config['custom_llm'] = custom_llm
        return self

    def set_voice(self, voice_id, stability=0.5, similarity_boost=0.8, model_id=None):
        """Set TTS voice configuration."""
        tts_config = self.config['conversation_config']['tts']
        tts_config['voice_id'] = voice_id
        tts_config['voice_settings'] = {
            'stability': stability,
            'similarity_boost': similarity_boost,
        }
        if model_id:
            tts_config['model_id'] = model_id
        return self

    def set_language(self, language_code):
        """Set conversation language."""
        agent_config = self.config['conversation_config']['agent']
        agent_config['language'] = language_code
        return self

    def set_audio_format(self, output_format='ulaw_8000', sample_rate=8000):
        """Set audio output format."""
        tts_config = self.config['conversation_config']['tts']
        tts_config['output_format'] = output_format
        tts_config['sample_rate'] = sample_rate
        return self

    def add_tool(self, tool_config):
        """Add a tool to the agent configuration."""
        agent_config = self.config['conversation_config']['agent']
        if 'tools' not in agent_config:
            agent_config['tools'] = []
        agent_config['tools'].append(tool_config)
        return self

    def set_twilio_outbound(self, phone_number, agent_id):
        """Configure Twilio outbound calling."""
        if 'twilio' not in self.config['platform_settings']:
            self.config['platform_settings']['twilio'] = {}
        self.config['platform_settings']['twilio']['outbound_call'] = {
            'phone_number': phone_number,
            'agent_id': agent_id,
        }
        return self

    def build(self):
        """Build and return the configuration dictionary."""
        return self.config
