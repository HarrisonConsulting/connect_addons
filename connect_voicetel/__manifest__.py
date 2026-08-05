# -*- coding: utf-8 -*-

{
    'name': 'Connect VoiceTel',
    'version': '1.2.4',
    'author': 'Oduist',
    'maintainer': 'Oduist',
    'price': 0,
    'currency': 'EUR',
    'support': 'support@oduist.com',
    'license': 'OPL-1',
    'category': 'Phone',
    'summary': 'VoiceTel (VoiceML) provider for Connect',
    'description': """
VoiceTel provider for Connect
=============================

Adds VoiceTel as a telephony provider in Connect settings:
- VoiceTel-labeled credentials (Account SID, API Key, API Secret, REST host)
- Routes the Connect REST client to the VoiceML API
- Migration uses Connect's native Migrate Account wizard, no external tooling
- Browser softphone (SIP.js over WSS) in the same transport registry as Twilio
    """,
    'depends': ['connect'],
    'data': [
        # Security
        'security/ir.model.access.csv',
        # Views
        'views/settings.xml',
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
