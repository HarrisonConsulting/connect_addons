# -*- coding: utf-8 -*-
{
    'name': 'Connect Voice Callout',
    'version': '18.0.1.0.0',
    'category': 'Productivity/Voice AI',
    'summary': 'Voice AI integration for Connect Callout campaigns',
    'description': """
Connect Voice Callout
=====================

Integrates the voice_base framework with Connect Callout campaigns,
providing AI-powered outbound calling capabilities.

Features:
- Dual call mode: Traditional TwiML or AI Agent
- TTS audio file generation for prompts
- AI agent configuration per callout
- Dynamic variable personalization
- Provider-agnostic architecture

This module replaces the legacy connect_elevenlabs_callout module
with a provider-agnostic implementation.
    """,
    'author': 'Harrison Consulting, LLC',
    'website': 'https://www.harrison.consulting',
    'license': 'OPL-1',
    'depends': [
        'connect_voice',
        'connect_callout',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/callout_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
