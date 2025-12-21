{
    'name': 'Connect Voice Agents',
    'version': '18.0.1.1.0',
    'category': 'Productivity/Voice AI',
    'summary': 'Voice AI agents for Connect telephony integration',
    'description': """
Connect Voice Agents
====================

Provides voice AI agents for Connect telephony integration using the
voice_base framework.

Features:
- Voice agents for Connect extensions and callflows
- WebSocket streaming for Twilio integration
- Post-call webhook handling
- Full voice configuration via voice.agent.mixin
- Provider-agnostic voice AI (ElevenLabs, VAPI, etc.)

This module bridges Connect telephony with pluggable voice AI providers.
    """,
    'author': 'Harrison Consulting, LLC',
    'website': 'https://www.harrison.consulting',
    'license': 'OPL-1',
    'depends': [
        'voice_base',
        'connect',
    ],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'views/connect_voice_agent_views.xml',
        'views/menus.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
