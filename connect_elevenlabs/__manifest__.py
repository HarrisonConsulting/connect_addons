# -*- coding: utf-8 -*

{
    'name': 'Connect ElevenLabs',
    'version': '1.4.2',
    'author': 'Oduist',
    'maintainer': 'Oduist',
    'price': 0,
    'currency': 'EUR',
    'support': 'support@oduist.com',
    'license': 'OPL-1',
    'category': 'Phone',
    'summary': 'Connect ElevenLabs Conversational AI Integration',
    'description': """
ElevenLabs Conversational AI Integration for Odoo
==================================================

Features:
- Sync AI agents from ElevenLabs
- Configure voice, LLM, and conversation settings
- Manage tools and knowledge bases
- Telephony integration with Twilio
- Full conversation tracking and history
    """,
    'depends': ['connect', 'calendar', 'mail'],
    'external_dependencies': {
        'python': ['elevenlabs'],
    },
    'data': [
        # Data
        'data/tools.xml',
        'data/agent_templates.xml',
        # Security
        'security/admin.xml',
        'security/user.xml',
        'security/webhook.xml',
        # Views
        'views/call.xml',
        'views/settings.xml',
        'views/voice.xml',
        'views/callflow.xml',
        'views/user.xml',
        'views/agent.xml',
        'views/agent_prompt.xml',
        'views/agent_transfer.xml',
        'views/agent_template.xml',
        'views/agent_tool.xml',
        'views/agent_tool_params.xml',
        'views/number.xml',
        'views/phone_registration.xml',
        'views/recording.xml',
        'views/documentation.xml',
        'views/system_message.xml',
    ],
    'demo': [],
    'installable': True,
    'application': False,
    'auto_install': False,
    'images': ['static/description/logo.png'],
    'assets': {
        'web.assets_backend': [],
    }
}
