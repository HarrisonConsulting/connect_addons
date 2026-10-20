# -*- coding: utf-8 -*-
{
    'name': 'Oduist Connect VoiceML',
    'version': '19.0.1.2.0',
    'author': 'Oduist',
    'category': 'Phone',
    'summary': 'VoiceML integration for Oduist Connect',
    'depends': ['connect'],
    'external_dependencies': {
        'python': ['voiceml>=0.9.3'],
    },
    'data': [
        'security/access_rules.xml',
        'views/menu.xml',
        'views/settings_views.xml',
        'views/application_views.xml',
        'views/domain_views.xml',
        'views/user_views.xml',
        'views/exten_views.xml',
        'views/callflow_views.xml',
        'views/number_views.xml',
        'views/outgoing_callerid_views.xml',
        'data/application.xml',
        'data/revoke_credentials.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'connect_voiceml/static/src/lib/jssip.min.js',
            'connect_voiceml/static/src/js/utils.js',
            'connect_voiceml/static/src/js/main.js',
            'connect_voiceml/static/src/components/**/*',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'Other proprietary',
}
