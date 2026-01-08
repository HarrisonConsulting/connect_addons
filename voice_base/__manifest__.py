{
    'name': 'Voice AI Base',
    'version': '18.0.1.2.3',
    'category': 'Productivity/Voice AI',
    'summary': 'Base framework for pluggable voice AI providers',
    'description': """
Voice AI Base Framework
========================

This module provides the base framework for pluggable voice AI providers.
It defines:
- Base provider model with common fields and interface
- Shared models (tools, MCP servers, conversations, voices)
- Common agent configuration mixin
- Universal constants and enumerations

This is a framework module - install provider-specific modules like
voice_elevenlabs, voice_vapi, etc. to add actual functionality.
    """,
    'author': 'Harrison Consulting, LLC',
    'website': 'https://www.harrison.consulting',
    'license': 'OPL-1',
    'depends': [
        'base',
        'mail',
    ],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/voice_data.xml',
        'views/voice_provider_views.xml',
        'views/voice_mcp_server_views.xml',
        'views/voice_tool_views.xml',
        'views/voice_knowledge_base_views.xml',
        'views/voice_conversation_views.xml',
        'views/voice_voice_views.xml',
        'views/voice_tts_file_views.xml',
        'views/menus.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}
