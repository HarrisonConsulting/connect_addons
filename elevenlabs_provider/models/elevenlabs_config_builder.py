# -*- coding: utf-8 -*-
"""
ElevenLabs Configuration Builder

Utility methods for building ElevenLabs agent configuration objects.
This includes conversation config, platform settings, tools, MCP servers, etc.
"""
import logging
from odoo import models, api

logger = logging.getLogger(__name__)


class ElevenLabsConfigBuilder(models.AbstractModel):
    """
    Abstract model providing utility methods for building ElevenLabs
    configuration structures.

    This is a utility model that can be inherited by agent models
    to simplify configuration building.
    """
    _name = 'elevenlabs.config.builder'
    _description = 'ElevenLabs Configuration Builder Utility'

    @api.model
    def build_conversation_config(self, **params):
        """
        Build a conversation config dict for ElevenLabs agent creation/update.

        Args:
            **params: Configuration parameters including:
                - voice_id: Voice ID
                - llm_model: LLM model name
                - tts_model: TTS model name
                - system_prompt: Agent prompt
                - first_message: First message
                - language: Language code
                - temperature: LLM temperature
                - max_tokens: Max LLM tokens
                - stability: Voice stability
                - similarity_boost: Voice similarity
                - speed: Voice speed
                - turn_timeout: Turn timeout in seconds
                - max_duration_seconds: Max conversation duration
                - tools: List of tool dicts
                - mcp_servers: List of MCP server configs
                - knowledge_base_ids: List of knowledge base document IDs

        Returns:
            dict: ElevenLabs conversation config structure
        """
        config = {
            'conversation_config': {
                # ASR (Automatic Speech Recognition)
                'asr': {
                    'quality': params.get('asr_quality', 'high'),
                    'provider': params.get('asr_provider', 'elevenlabs'),
                },

                # TTS (Text-to-Speech)
                'tts': {
                    'model_id': params.get('tts_model', 'eleven_flash_v2_5'),
                    'voice_id': params.get('voice_id'),
                },

                # Turn settings
                'turn': {
                    'timeout': params.get('turn_timeout', 7.0),
                },

                # Conversation settings
                'conversation': {
                    'max_duration_seconds': params.get('max_duration_seconds', 600),
                },

                # Agent settings
                'agent': {
                    'prompt': {
                        'llm': params.get('llm_model', 'gpt-4o'),
                        'temperature': params.get('temperature', 0.7),
                        'max_tokens': params.get('max_tokens', -1),
                        'prompt': params.get('system_prompt', 'You are a helpful assistant.'),
                    },
                    'first_message': params.get('first_message', 'Hello! How can I help you today?'),
                    'language': params.get('language', 'en'),
                },
            }
        }

        # Add voice settings if provided
        voice_settings = {}
        if 'stability' in params:
            voice_settings['stability'] = params['stability']
        if 'similarity_boost' in params:
            voice_settings['similarity_boost'] = params['similarity_boost']
        if 'speed' in params:
            voice_settings['speed'] = params['speed']

        if voice_settings:
            config['conversation_config']['tts']['voice_settings'] = voice_settings

        # Add tools if provided
        if params.get('tools'):
            config['conversation_config']['tools'] = params['tools']

        # Add MCP servers if provided
        if params.get('mcp_servers'):
            config['conversation_config']['mcp_servers'] = params['mcp_servers']

        # Add knowledge base if provided
        if params.get('knowledge_base_ids'):
            config['conversation_config']['knowledge_base'] = {
                'document_ids': params['knowledge_base_ids'],
                'rag_enabled': True,
            }

        return config

    @api.model
    def build_platform_settings(self, **params):
        """
        Build platform settings dict for ElevenLabs agent.

        Args:
            **params: Platform settings including:
                - widget_variant: Widget variant (full/compact)
                - primary_color: Primary color hex
                - feedback_mode: Feedback mode
                - record_voice: Whether to record voice
                - retention_days: Voice retention days
                - post_call_webhook_url: Webhook URL for post-call events
                - webhook_secret: Secret for webhook validation

        Returns:
            dict: Platform settings structure
        """
        settings = {
            'platform_settings': {
                'widget': {
                    'variant': params.get('widget_variant', 'compact'),
                    'primary_color': params.get('primary_color', '#875A7B'),
                    'feedback_mode': params.get('feedback_mode', 'during'),
                },
                'privacy': {
                    'record_voice': params.get('record_voice', True),
                    'retention_days': params.get('retention_days', 30),
                },
            }
        }

        # Add webhooks if provided
        if params.get('post_call_webhook_url'):
            settings['platform_settings']['webhooks'] = {
                'post_call': {
                    'url': params['post_call_webhook_url'],
                }
            }
            if params.get('webhook_secret'):
                settings['platform_settings']['webhooks']['post_call']['secret'] = params['webhook_secret']

        return settings

    @api.model
    def tool_to_elevenlabs_format(self, tool):
        """
        Convert a voice.tool record to ElevenLabs tool format.

        Args:
            tool (voice.tool): Tool record

        Returns:
            dict: ElevenLabs tool configuration
        """
        if tool.tool_type == 'webhook':
            return self._build_webhook_tool(tool)
        elif tool.tool_type == 'client':
            return self._build_client_tool(tool)
        elif tool.tool_type == 'system':
            return self._build_system_tool(tool)
        else:
            logger.warning("Unknown tool type: %s", tool.tool_type)
            return None

    def _build_webhook_tool(self, tool):
        """Build webhook tool config."""
        config = {
            'type': 'webhook',
            'name': tool.name,
            'description': tool.description,
            'url': tool.webhook_url,
            'method': tool.webhook_method,
            'response_timeout_secs': tool.timeout_seconds or 30,
        }

        # Add parameters schema
        if tool.parameter_ids:
            config['parameters'] = tool.get_tool_schema()['function']['parameters']

        # Add headers if configured
        if tool.webhook_headers:
            try:
                import json
                headers = json.loads(tool.webhook_headers)
                # Convert headers to auth config if needed
                # For now, just log that we need to handle this
                logger.debug("Webhook headers for tool %s: %s", tool.name, headers)
            except Exception as e:
                logger.warning("Could not parse webhook headers for tool %s: %s", tool.name, e)

        return config

    def _build_client_tool(self, tool):
        """Build client tool config."""
        config = {
            'type': 'client',
            'name': tool.name,
            'description': tool.description,
            'wait_for_response': tool.wait_for_response,
        }

        # Add parameters schema
        if tool.parameter_ids:
            config['parameters'] = tool.get_tool_schema()['function']['parameters']

        return config

    def _build_system_tool(self, tool):
        """Build system tool config."""
        config = {
            'type': 'system',
            'system_tool_type': tool.system_tool_type,
        }

        return config

    @api.model
    def mcp_server_to_elevenlabs_format(self, mcp_server):
        """
        Convert a voice.mcp.server record to ElevenLabs MCP config.

        Args:
            mcp_server (voice.mcp.server): MCP server record

        Returns:
            dict: ElevenLabs MCP server configuration
        """
        config = {
            'url': mcp_server.url,
            'transport': mcp_server.transport_type,
            'name': mcp_server.name,
        }

        # Add auth if configured
        if mcp_server.auth_type != 'none':
            if mcp_server.auth_type == 'bearer':
                config['auth'] = {
                    'type': 'bearer',
                    'token': mcp_server.auth_token,
                }
            elif mcp_server.auth_type == 'api_key':
                config['auth'] = {
                    'type': 'header',
                    'name': 'X-API-Key',
                    'value': mcp_server.auth_token,
                }
            elif mcp_server.auth_type == 'custom':
                config['auth'] = {
                    'type': 'header',
                    'name': mcp_server.auth_header_name or 'Authorization',
                    'value': mcp_server.auth_token,
                }

        # Add tool approval settings
        if hasattr(mcp_server, 'approval_mode'):
            config['tool_approval'] = {
                'mode': mcp_server.approval_mode,
            }
            if mcp_server.approval_mode == 'whitelist' and hasattr(mcp_server, 'allowed_tool_names'):
                config['tool_approval']['allowed_tools'] = mcp_server.allowed_tool_names.split(',')

        return config
