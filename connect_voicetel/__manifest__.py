# -*- coding: utf-8 -*-

{
    'name': 'Connect VoiceTel',
    'version': '1.2.9',
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
- Serves Twilio's API, webhooks and media on VoiceTel's endpoint
- Numbers are ported carrier-side, then synced into Connect
- Browser softphone (SIP.js over WSS) in the same transport registry as Twilio
    """,
    'depends': ['connect', 'connect_twilio'],
    'data': [
        # Security
        'security/ir.model.access.csv',
        # Views
        'views/settings.xml',
        'views/res_config_settings_views.xml',
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
