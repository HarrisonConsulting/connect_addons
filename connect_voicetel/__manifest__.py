# -*- coding: utf-8 -*-

{
    'name': 'Connect VoiceTel (retired)',
    'version': '19.0.3.0.0',
    'author': 'Oduist',
    'maintainer': 'Harrison Consulting, LLC',
    'website': 'https://www.harrison.consulting',
    'price': 0,
    'currency': 'EUR',
    'support': 'support@oduist.com',
    'license': 'OPL-1',
    'category': 'Phone',
    'summary': 'Retired: replaced by connect_voiceml',
    'description': """
Connect VoiceTel (retired)
==========================

connect_voiceml replaces this module. This release ships no models,
views, routes or assets. Upgrading to it removes VoiceTel's menus, views
and fields from the database without calling the VoiceTel API, and keeps
a copy of the VoiceTel settings and user columns. Uninstall it after the
upgrade.
    """,
    'depends': ['connect'],
    'data': [],
    'installable': True,
    'application': False,
    'auto_install': False,
}
