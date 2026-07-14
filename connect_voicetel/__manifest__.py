# -*- coding: utf-8 -*-

{
    'name': 'Connect VoiceTel',
    'version': '1.0.0',
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
- Dry-run launcher for the twilio-migration tool
    """,
    'depends': ['connect'],
    'data': [
        # Views
        'views/settings.xml',
    ],
    'demo': [],
    'installable': True,
    'application': False,
    'auto_install': False,
}
