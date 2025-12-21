{
    'name': 'Crew Voice Integration',
    'version': '18.0.1.0.2',
    'category': 'Productivity/Voice AI',
    'summary': 'Voice AI capabilities for Crew agents',
    'description': """
Crew Voice Integration
======================

Extends Crew agents with voice conversation capabilities using the
voice_base framework.

Features:
- Enable voice conversations for Crew agents
- Full voice configuration via voice.agent.mixin
- Provider-agnostic voice AI (ElevenLabs, VAPI, etc.)
- Tool support for voice agents
- Knowledge base integration
- MCP server support

This module bridges Crew AI agents with pluggable voice AI providers,
enabling browser-based voice conversations with your AI agents.
    """,
    'author': 'Harrison Consulting, LLC',
    'website': 'https://www.harrison.consulting',
    'license': 'OPL-1',
    'depends': [
        'voice_base',
        'crew',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/crew_agent_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
