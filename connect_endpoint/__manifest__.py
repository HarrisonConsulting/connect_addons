# -*- coding: utf-8 -*-

{
    'name': 'Connect Desk Phones',
    'version': '19.0.1.0.0',
    'author': 'Harrison Consulting, LLC',
    'website': 'https://www.harrison.consulting',
    'license': 'OPL-1',
    'category': 'Phone',
    'summary': 'Desk phones whose SIP accounts each register to a system',
    'depends': ['connect'],
    'data': [
        'security/ir.model.access.csv',
        'views/endpoint_views.xml',
        'views/menu.xml',
    ],
    'installable': True,
    'application': False,
}
