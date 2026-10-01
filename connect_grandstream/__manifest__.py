# -*- coding: utf-8 -*-

{
    'name': 'Connect Grandstream',
    'version': '19.0.1.0.0',
    'author': 'Harrison Consulting, LLC',
    'website': 'https://www.harrison.consulting',
    'license': 'OPL-1',
    'category': 'Phone',
    'summary': 'Grandstream desk phones on Connect endpoints',
    'description': """
Grandstream adapter for Connect endpoints.

A Grandstream endpoint offers 16 SIP account slots and 12 line keys.
Provisioning values come from the stored account. Registration events
update that account. The module does not open a connection to the phone.
    """,
    'depends': ['connect_endpoint'],
    'data': [],
    'installable': True,
    'application': False,
}
