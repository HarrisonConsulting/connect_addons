# -*- coding: utf-8 -*-
"""
Shared constants for Voice AI framework.

This module provides the single source of truth for:
- LLM models
- TTS models
- Languages
- Audio formats
- Sync statuses
"""

# LLM Models (26 models across GPT, Claude, Gemini, Grok)
LLM_MODEL_LIST = [
    # OpenAI GPT
    ('gpt-4o', 'GPT-4o'),
    ('gpt-4o-mini', 'GPT-4o Mini'),
    ('gpt-4-turbo', 'GPT-4 Turbo'),
    ('gpt-4', 'GPT-4'),
    ('gpt-3.5-turbo', 'GPT-3.5 Turbo'),

    # Anthropic Claude
    ('claude-3-5-sonnet-20241022', 'Claude 3.5 Sonnet'),
    ('claude-3-opus-20240229', 'Claude 3 Opus'),
    ('claude-3-sonnet-20240229', 'Claude 3 Sonnet'),
    ('claude-3-haiku-20240307', 'Claude 3 Haiku'),

    # Google Gemini
    ('gemini-1.5-pro', 'Gemini 1.5 Pro'),
    ('gemini-1.5-flash', 'Gemini 1.5 Flash'),
    ('gemini-pro', 'Gemini Pro'),

    # xAI Grok
    ('grok-beta', 'Grok Beta'),
    ('grok-2', 'Grok 2'),

    # Meta Llama
    ('llama-3.1-405b', 'Llama 3.1 405B'),
    ('llama-3.1-70b', 'Llama 3.1 70B'),
    ('llama-3.1-8b', 'Llama 3.1 8B'),

    # Mistral
    ('mistral-large', 'Mistral Large'),
    ('mistral-medium', 'Mistral Medium'),
    ('mistral-small', 'Mistral Small'),

    # Cohere
    ('command-r-plus', 'Command R+'),
    ('command-r', 'Command R'),
    ('command', 'Command'),

    # Other
    ('deepseek-chat', 'DeepSeek Chat'),
    ('qwen-turbo', 'Qwen Turbo'),
    ('yi-large', 'Yi Large'),
]

# TTS Models (primarily ElevenLabs, extensible by providers)
TTS_MODEL_LIST = [
    ('eleven_multilingual_v2', 'Eleven Multilingual v2'),
    ('eleven_turbo_v2_5', 'Eleven Turbo v2.5'),
    ('eleven_turbo_v2', 'Eleven Turbo v2'),
    ('eleven_monolingual_v1', 'Eleven Monolingual v1'),
    ('eleven_flash_v2_5', 'Eleven Flash v2.5'),
]

# Languages (30 languages with ISO codes)
LANGUAGE_LIST = [
    ('en', 'English'),
    ('es', 'Spanish'),
    ('fr', 'French'),
    ('de', 'German'),
    ('it', 'Italian'),
    ('pt', 'Portuguese'),
    ('pl', 'Polish'),
    ('nl', 'Dutch'),
    ('sv', 'Swedish'),
    ('da', 'Danish'),
    ('fi', 'Finnish'),
    ('no', 'Norwegian'),
    ('cs', 'Czech'),
    ('el', 'Greek'),
    ('hu', 'Hungarian'),
    ('ro', 'Romanian'),
    ('ru', 'Russian'),
    ('uk', 'Ukrainian'),
    ('tr', 'Turkish'),
    ('ar', 'Arabic'),
    ('zh', 'Chinese (Mandarin)'),
    ('ja', 'Japanese'),
    ('ko', 'Korean'),
    ('hi', 'Hindi'),
    ('bn', 'Bengali'),
    ('ta', 'Tamil'),
    ('th', 'Thai'),
    ('vi', 'Vietnamese'),
    ('id', 'Indonesian'),
    ('ms', 'Malay'),
]

# Sync Status
SYNC_STATUS_LIST = [
    ('pending', 'Pending'),
    ('syncing', 'Syncing'),
    ('synced', 'Synced'),
    ('error', 'Error'),
]

# Audio Formats
AUDIO_FORMAT_LIST = [
    ('mp3', 'MP3'),
    ('wav', 'WAV'),
    ('pcm_16000', 'PCM 16kHz'),
    ('pcm_22050', 'PCM 22.05kHz'),
    ('pcm_24000', 'PCM 24kHz'),
    ('pcm_44100', 'PCM 44.1kHz'),
    ('ulaw_8000', 'uLaw 8kHz'),
]

# Conversation States
CONVERSATION_STATE_LIST = [
    ('pending', 'Pending'),
    ('connecting', 'Connecting'),
    ('active', 'Active'),
    ('completed', 'Completed'),
    ('failed', 'Failed'),
]

# Conversation Sources
CONVERSATION_SOURCE_LIST = [
    ('browser', 'Browser'),
    ('phone_inbound', 'Phone Inbound'),
    ('phone_outbound', 'Phone Outbound'),
]

# Tool Types
TOOL_TYPE_LIST = [
    ('webhook', 'Webhook'),
    ('client', 'Client'),
    ('system', 'System'),
]

# System Tool Types
SYSTEM_TOOL_TYPE_LIST = [
    ('end_call', 'End Call'),
    ('language_detection', 'Language Detection'),
    ('agent_transfer', 'Agent Transfer'),
    ('voicemail', 'Voicemail'),
    ('dtmf', 'DTMF'),
]

# MCP Transport Types
MCP_TRANSPORT_LIST = [
    ('sse', 'Server-Sent Events'),
    ('http', 'HTTP'),
]

# MCP Auth Types
MCP_AUTH_TYPE_LIST = [
    ('none', 'None'),
    ('bearer', 'Bearer Token'),
    ('api_key', 'API Key'),
    ('custom', 'Custom Header'),
]

# MCP Approval Modes
MCP_APPROVAL_MODE_LIST = [
    ('auto', 'Auto-approve all'),
    ('whitelist', 'Whitelist only'),
    ('manual', 'Manual approval'),
]

# HTTP Methods
HTTP_METHOD_LIST = [
    ('GET', 'GET'),
    ('POST', 'POST'),
    ('PUT', 'PUT'),
    ('PATCH', 'PATCH'),
    ('DELETE', 'DELETE'),
]

# Parameter Types (for tool parameters)
PARAMETER_TYPE_LIST = [
    ('string', 'String'),
    ('number', 'Number'),
    ('integer', 'Integer'),
    ('boolean', 'Boolean'),
    ('array', 'Array'),
    ('object', 'Object'),
]
