# -*- coding: utf-8 -*-
{
    'name': 'Connect S3 Recording Storage',
    'version': '19.0.1.3.7',
    'author': 'Harrison Consulting, LLC',
    'website': 'https://www.harrison.consulting',
    'license': 'OPL-1',
    'category': 'Phone',
    'summary': 'Store Connect call recordings and voicemails in S3-compatible object storage',
    'depends': ['connect', 'connect_pbx'],
    'external_dependencies': {
        'python': ['boto3'],
    },
    'data': [
        'data/ir_cron.xml',
        'security/ir.model.access.csv',
        'views/s3_migrate_wizard.xml',
        'views/settings.xml',
        'views/recording.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
