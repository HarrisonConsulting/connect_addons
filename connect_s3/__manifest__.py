# -*- coding: utf-8 -*-
{
    'name': 'Connect S3 Recording Storage',
    'version': '1.0.0',
    'author': 'Harrison Consulting, LLC',
    'website': 'https://www.harrison.consulting',
    'license': 'OPL-1',
    'category': 'Phone',
    'summary': 'Store Connect call recordings and voicemails in S3-compatible object storage',
    'depends': ['connect'],
    'external_dependencies': {
        'python': ['boto3'],
    },
    'data': [
        'views/settings.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
