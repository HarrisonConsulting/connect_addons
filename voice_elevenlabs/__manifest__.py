# -*- coding: utf-8 -*-
{
    'name': 'ElevenLabs Voice Provider',
    'version': '18.0.1.4.0',
    'category': 'Productivity/Voice AI',
    'summary': 'ElevenLabs integration for Voice AI framework',
    'description': """
ElevenLabs Voice AI Provider
=============================

Implements the voice.provider interface for ElevenLabs Conversational AI.

Features:
- Full voice library sync (5000+ voices)
- Tools integration (webhooks, client, system)
- MCP server support
- Knowledge base integration
- Twilio telephony integration
- Phone number registration
- Post-call webhooks
- Conversation tracking

This module implements the abstract voice_base framework for ElevenLabs.
    """,
    'author': 'Harrison Consulting, LLC',
    'website': 'https://www.harrison.consulting',
    'license': 'OPL-1',
    'depends': [
        'voice_base',
        'connect',  # For phone number management
    ],
    'external_dependencies': {
        'python': ['elevenlabs'],
    },
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/provider_data.xml',
        'views/elevenlabs_provider_views.xml',
        'views/elevenlabs_phone_views.xml',
        'views/settings_views.xml',
        'wizards/migration_wizard_views.xml',
        'views/menus.xml',
    ],
    'pre_init_hook': 'pre_init_hook',
    'post_init_hook': 'post_init_hook',
    'uninstall_hook': 'uninstall_hook',
    'installable': True,
    'application': False,
    'auto_install': False,
}
