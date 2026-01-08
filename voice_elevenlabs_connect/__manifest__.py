# -*- coding: utf-8 -*-
{
    'name': 'ElevenLabs Voice - Connect Integration',
    'version': '18.0.1.0.2',
    'category': 'Productivity/Voice AI',
    'summary': 'Bridge module connecting ElevenLabs voice provider with Connect telephony',
    'description': """
ElevenLabs Voice - Connect Integration
======================================

This bridge module integrates the ElevenLabs voice provider with Connect
telephony system, providing:

- Phone number registration with Twilio via Connect's outgoing_callerid
- Twilio credentials management via Connect settings
- Outbound calling using Connect's Twilio integration
- Settings UI under Connect Settings menu

Architecture:
- Extends voice_elevenlabs with Connect-specific functionality
- Extends connect.settings with ElevenLabs configuration fields
- Links elevenlabs.phone.registration to connect.outgoing_callerid
- Provides menus under Connect Settings

This module is optional - voice_elevenlabs can be used standalone for
TTS/STT and agent management without Connect telephony integration.
    """,
    'author': 'Harrison Consulting, LLC',
    'website': 'https://www.harrison.consulting',
    'license': 'OPL-1',
    'depends': [
        'voice_elevenlabs',
        'connect',
    ],
    'data': [
        'views/settings_views.xml',
        'views/phone_views.xml',
        'views/menus.xml',
    ],
    'pre_init_hook': 'pre_init_hook',
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
    'auto_install': False,
}
