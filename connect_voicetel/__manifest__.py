# -*- coding: utf-8 -*-

{
    'name': 'Connect VoiceTel',
    'version': '19.0.2.0.0',
    'author': 'Oduist',
    'maintainer': 'Harrison Consulting, LLC',
    'website': 'https://www.harrison.consulting',
    'price': 0,
    'currency': 'EUR',
    'support': 'support@oduist.com',
    'license': 'OPL-1',
    'category': 'Phone',
    'summary': 'VoiceTel provider for Connect',
    'description': """
VoiceTel provider for Connect
=============================

Adds VoiceTel as a standalone telephony provider in Connect:
- Its own PBX configuration models (numbers, extensions, call flows,
  applications, SIP domains, caller IDs) in a VoiceTel submenu
- Its own REST client (the voiceml SDK) and call-control XML builder
- Webhooks signed and verified with the VoiceTel API key
- Browser softphone (SIP.js over WSS) in the same transport registry as
  every other Connect provider
    """,
    'depends': ['connect'],
    'external_dependencies': {
        'python': ['voiceml'],
    },
    'data': [
        # Security
        'security/ir.model.access.csv',
        # Views
        'views/menu.xml',
        'views/settings.xml',
        'views/application_views.xml',
        'views/domain_views.xml',
        'views/user_views.xml',
        'views/exten_views.xml',
        'views/callflow_views.xml',
        'views/number_views.xml',
        'views/call_views.xml',
        'views/message_views.xml',
        'views/outgoing_callerid_views.xml',
        # Data
        'data/application.xml',
    ],
    'assets': {
        'web.assets_backend': [
            '/connect_voicetel/static/src/components/phone/phone/transports/*',
        ],
    },
    'demo': [],
    'installable': True,
    'application': False,
    'auto_install': False,
}
