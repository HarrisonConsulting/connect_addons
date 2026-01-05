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

    Maps generic voice_base field names to ElevenLabs-specific API values.
    """
    _name = 'elevenlabs.config.builder'
    _description = 'ElevenLabs Configuration Builder Utility'

    # === Field Name Mappings (generic -> ElevenLabs API) ===

    # System tool type mapping
    SYSTEM_TOOL_TYPE_MAP = {
        'end_call': 'end_call',
        'transfer_call': 'transfer_to_human',  # Maps to transfer_to_human or agent_transfer based on destination
        'detect_voicemail': 'voicemail_detection',
        'play_tones': 'play_dtmf',
        'pause_response': 'skip_turn',
        'detect_language': 'language_detection',
    }

    # Approval policy mapping (generic -> ElevenLabs)
    APPROVAL_POLICY_MAP = {
        'auto': 'auto_approve_all',
        'manual': 'require_approval_all',
        'per_tool': 'require_approval_per_tool',
    }

    # Execution sound mapping (generic -> ElevenLabs)
    EXECUTION_SOUND_MAP = {
        'none': 'none',
        'typing': 'typing',
        'hold_music': 'hold_music',
        'processing': 'processing',
    }

    # Approval status mapping (generic -> ElevenLabs)
    APPROVAL_STATUS_MAP = {
        'approved': 'auto_approved',
        'requires_approval': 'requires_approval',
        'disabled': 'disabled',
    }

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
        """Build webhook tool config matching ElevenLabs API schema."""
        import json

        config = {
            'type': 'webhook',
            'name': tool.name,
            'description': tool.description or '',
            'api_schema': {
                'url': tool.webhook_url,
                'method': tool.webhook_method or 'POST',
            },
            'response_timeout_secs': tool.timeout_seconds or 30,
        }

        # Add parameters schema
        if tool.parameter_ids:
            config['api_schema']['request_body'] = {
                'properties': {},
                'required': [],
            }
            for param in tool.parameter_ids:
                config['api_schema']['request_body']['properties'][param.name] = {
                    'type': param.parameter_type,
                    'description': param.description or '',
                }
                if param.enum_values:
                    config['api_schema']['request_body']['properties'][param.name]['enum'] = \
                        [v.strip() for v in param.enum_values.split(',')]
                if param.required:
                    config['api_schema']['request_body']['required'].append(param.name)

        # Add request headers
        if tool.webhook_headers:
            try:
                headers = json.loads(tool.webhook_headers)
                config['api_schema']['request_headers'] = headers
            except Exception as e:
                logger.warning("Could not parse webhook headers for tool %s: %s", tool.name, e)

        # Add authentication configuration
        auth_config = self._build_webhook_auth_config(tool)
        if auth_config:
            config['api_schema']['auth'] = auth_config

        # Add pre-tool speech configuration (generic -> ElevenLabs API)
        if tool.pre_tool_message:
            config['pre_tool_speech'] = tool.pre_tool_message
        if tool.force_pre_tool_message:
            config['force_pre_tool_speech'] = True

        # Add tool call sound (generic -> ElevenLabs API)
        if tool.execution_sound and tool.execution_sound != 'none':
            config['tool_call_sound'] = self.EXECUTION_SOUND_MAP.get(
                tool.execution_sound, tool.execution_sound)
            if tool.execution_sound_behavior:
                config['tool_call_sound_behavior'] = tool.execution_sound_behavior

        return config

    def _build_webhook_auth_config(self, tool):
        """Build authentication configuration for webhook tool."""
        if not tool.webhook_auth_type or tool.webhook_auth_type == 'none':
            return None

        if tool.webhook_auth_type == 'bearer':
            return {
                'type': 'bearer',
                'token': tool.webhook_auth_token,
            }
        elif tool.webhook_auth_type == 'basic':
            return {
                'type': 'basic',
                'username': tool.webhook_auth_username,
                'password': tool.webhook_auth_token,
            }
        elif tool.webhook_auth_type == 'oauth2_client_credentials':
            auth = {
                'type': 'oauth2_client_credentials',
                'client_id': tool.webhook_auth_username,
                'client_secret': tool.webhook_auth_token,
                'token_url': tool.webhook_oauth2_token_url,
            }
            if tool.webhook_oauth2_scopes:
                auth['scopes'] = tool.webhook_oauth2_scopes
            if tool.webhook_oauth2_extra_params:
                try:
                    import json
                    auth['extra_params'] = json.loads(tool.webhook_oauth2_extra_params)
                except Exception:
                    pass
            return auth
        elif tool.webhook_auth_type == 'oauth2_jwt':
            auth = {
                'type': 'oauth2_jwt',
                'jwt_secret': tool.webhook_jwt_secret,
                'token_url': tool.webhook_oauth2_token_url,
                'algorithm': tool.webhook_jwt_algorithm or 'HS256',
            }
            if tool.webhook_jwt_issuer:
                auth['issuer'] = tool.webhook_jwt_issuer
            if tool.webhook_jwt_audience:
                auth['audience'] = tool.webhook_jwt_audience
            if tool.webhook_jwt_subject:
                auth['subject'] = tool.webhook_jwt_subject
            return auth
        elif tool.webhook_auth_type == 'custom_headers':
            if tool.webhook_headers:
                try:
                    import json
                    return {
                        'type': 'custom',
                        'headers': json.loads(tool.webhook_headers),
                    }
                except Exception:
                    pass
        return None

    def _build_client_tool(self, tool):
        """Build client tool config."""
        config = {
            'type': 'client',
            'name': tool.name,
            'description': tool.description or '',
            'wait_for_response': tool.wait_for_response,
        }

        # Add parameters schema
        if tool.parameter_ids:
            config['parameters'] = {
                'type': 'object',
                'properties': {},
                'required': [],
            }
            for param in tool.parameter_ids:
                config['parameters']['properties'][param.name] = {
                    'type': param.parameter_type,
                    'description': param.description or '',
                }
                if param.enum_values:
                    config['parameters']['properties'][param.name]['enum'] = \
                        [v.strip() for v in param.enum_values.split(',')]
                if param.required:
                    config['parameters']['required'].append(param.name)

        # Add pre-tool speech configuration (generic -> ElevenLabs API)
        if tool.pre_tool_message:
            config['pre_tool_speech'] = tool.pre_tool_message
        if tool.force_pre_tool_message:
            config['force_pre_tool_speech'] = True

        # Add tool call sound (generic -> ElevenLabs API)
        if tool.execution_sound and tool.execution_sound != 'none':
            config['tool_call_sound'] = self.EXECUTION_SOUND_MAP.get(
                tool.execution_sound, tool.execution_sound)
            if tool.execution_sound_behavior:
                config['tool_call_sound_behavior'] = tool.execution_sound_behavior

        return config

    def _build_system_tool(self, tool):
        """
        Build system tool config matching ElevenLabs API schema.

        Maps generic voice_base system tool types to ElevenLabs-specific types.
        """
        # Map generic system tool type to ElevenLabs-specific type
        elevenlabs_tool_type = self.SYSTEM_TOOL_TYPE_MAP.get(
            tool.system_tool_type, tool.system_tool_type)

        # Special case: transfer_call maps differently based on destination type
        if tool.system_tool_type == 'transfer_call':
            if tool.transfer_destination_type == 'agent':
                elevenlabs_tool_type = 'agent_transfer'
            else:
                elevenlabs_tool_type = 'transfer_to_human'

        config = {
            'type': 'system',
            'system_tool_type': elevenlabs_tool_type,
        }

        # Add system-tool-specific configuration
        if tool.system_tool_type == 'end_call':
            # End call has no additional config
            pass

        elif tool.system_tool_type == 'transfer_call':
            # Map generic transfer fields to ElevenLabs-specific fields
            if tool.transfer_destination_type == 'agent':
                # Agent transfer
                config['agent_id'] = tool.transfer_destination
            else:
                # Phone transfer (transfer_to_human)
                config['phone_number'] = tool.transfer_destination
                if tool.transfer_message_caller:
                    config['customer_message'] = tool.transfer_message_caller
                if tool.transfer_message_recipient:
                    config['operator_message'] = tool.transfer_message_recipient

        elif tool.system_tool_type == 'detect_voicemail':
            # Map to ElevenLabs voicemail_detection
            config['action'] = tool.voicemail_action or 'end_call'
            if tool.voicemail_message:
                config['voicemail_message'] = tool.voicemail_message

        elif tool.system_tool_type == 'play_tones':
            # Map generic tone fields to ElevenLabs DTMF fields
            if tool.tone_sequence:
                config['dtmf_tones'] = tool.tone_sequence
            if tool.tone_out_of_band:
                config['use_out_of_band_dtmf'] = True

        elif tool.system_tool_type == 'pause_response':
            # Maps to ElevenLabs skip_turn - no additional config
            pass

        elif tool.system_tool_type == 'detect_language':
            # Maps to ElevenLabs language_detection - no additional config
            pass

        # Add pre-tool speech configuration (generic -> ElevenLabs API)
        if tool.pre_tool_message:
            config['pre_tool_speech'] = tool.pre_tool_message
        if tool.force_pre_tool_message:
            config['force_pre_tool_speech'] = True

        return config

    @api.model
    def mcp_server_to_elevenlabs_format(self, mcp_server):
        """
        Convert a voice.mcp.server record to ElevenLabs MCP config.

        Matches the ElevenLabs MCP Server API schema.

        Args:
            mcp_server (voice.mcp.server): MCP server record

        Returns:
            dict: ElevenLabs MCP server configuration
        """
        import json

        # Build config matching ElevenLabs POST /v1/convai/mcp-servers schema
        config = {
            'url': mcp_server.url,
            'name': mcp_server.name,
        }

        # Add description if available
        if mcp_server.description:
            config['description'] = mcp_server.description

        # Add transport (SSE or STREAMABLE_HTTP)
        if mcp_server.transport:
            transport_map = {
                'sse': 'SSE',
                'http': 'STREAMABLE_HTTP',
            }
            config['transport'] = transport_map.get(mcp_server.transport, 'SSE')

        # Add authentication (secret_token)
        if mcp_server.auth_type != 'none' and mcp_server.auth_token:
            config['secret_token'] = mcp_server.auth_token

        # Add custom headers
        if mcp_server.custom_headers:
            try:
                config['request_headers'] = json.loads(mcp_server.custom_headers)
            except Exception as e:
                logger.warning("Could not parse custom headers for MCP server %s: %s",
                             mcp_server.name, e)

        # Add approval policy (generic -> ElevenLabs API)
        if mcp_server.approval_policy:
            config['approval_policy'] = self.APPROVAL_POLICY_MAP.get(
                mcp_server.approval_policy, mcp_server.approval_policy)

        # Add tool approval hashes for per-tool approval
        if mcp_server.approval_policy == 'per_tool' and mcp_server.tool_override_ids:
            tool_approval_hashes = []
            for override in mcp_server.tool_override_ids:
                tool_approval_hashes.append({
                    'tool_name': override.tool_name,
                    'approval_status': self.APPROVAL_STATUS_MAP.get(
                        override.approval_status, override.approval_status),
                })
            config['tool_approval_hashes'] = tool_approval_hashes

        # Add execution settings (generic -> ElevenLabs API)
        if mcp_server.force_pre_tool_message:
            config['force_pre_tool_speech'] = True
        if mcp_server.disable_interruptions:
            config['disable_interruptions'] = True
        if mcp_server.disable_compression:
            config['disable_compression'] = True

        # Add execution mode
        if mcp_server.execution_mode and mcp_server.execution_mode != 'immediate':
            config['execution_mode'] = mcp_server.execution_mode

        # Add tool call sound (generic -> ElevenLabs API)
        if mcp_server.execution_sound and mcp_server.execution_sound != 'none':
            config['tool_call_sound'] = self.EXECUTION_SOUND_MAP.get(
                mcp_server.execution_sound, mcp_server.execution_sound)
            if mcp_server.execution_sound_behavior:
                config['tool_call_sound_behavior'] = mcp_server.execution_sound_behavior

        # Add per-tool config overrides
        if mcp_server.tool_override_ids:
            tool_config_overrides = []
            for override in mcp_server.tool_override_ids:
                tool_override = {'tool_name': override.tool_name}

                if override.description_override:
                    tool_override['description'] = override.description_override

                if override.parameters_override:
                    try:
                        tool_override['parameters'] = json.loads(override.parameters_override)
                    except Exception:
                        pass

                # Map generic pre-tool message -> ElevenLabs pre_tool_speech
                if override.pre_tool_message:
                    tool_override['pre_tool_speech'] = override.pre_tool_message
                if override.force_pre_tool_message:
                    tool_override['force_pre_tool_speech'] = True

                # Map generic execution sound -> ElevenLabs tool_call_sound
                if override.execution_sound and override.execution_sound != 'none':
                    tool_override['tool_call_sound'] = self.EXECUTION_SOUND_MAP.get(
                        override.execution_sound, override.execution_sound)
                    if override.execution_sound_behavior:
                        tool_override['tool_call_sound_behavior'] = override.execution_sound_behavior

                # Only add if there are actual overrides beyond tool_name
                if len(tool_override) > 1:
                    tool_config_overrides.append(tool_override)

            if tool_config_overrides:
                config['tool_config_overrides'] = tool_config_overrides

        return config

    @api.model
    def build_mcp_server_create_request(self, mcp_server):
        """
        Build the full request body for creating an MCP server in ElevenLabs.

        Args:
            mcp_server (voice.mcp.server): MCP server record

        Returns:
            dict: Request body for POST /v1/convai/mcp-servers
        """
        return {
            'config': self.mcp_server_to_elevenlabs_format(mcp_server)
        }
