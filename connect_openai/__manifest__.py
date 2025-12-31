# -*- coding: utf-8 -*-

{
    'name': 'Connect - OpenAI Integration',
    'version': '18.0.1.0.0',
    'category': 'Phone',
    'summary': 'Centralized OpenAI configuration for Connect telephony',
    'description': """
Connect - OpenAI Integration
============================

This bridge module integrates Connect telephony with the centralized OpenAI
configuration provided by the OpenAI Base module.

Features:
- Use centralized OpenAI API configuration for call transcription
- Use centralized OpenAI API configuration for call summarization
- Single point of configuration for all OpenAI integrations

Architecture:
- Extends connect.settings to use openai_base configuration
- Hides duplicate OpenAI settings from Connect settings page
- Clean separation: Connect doesn't need its own OpenAI config,
  this module bridges the two through inheritance
    """,
    'author': 'Harrison Consulting, LLC',
    'website': 'https://www.harrison.consulting',
    'license': 'OPL-1',
    'depends': ['connect', 'openai_base'],
    'data': [
        'views/settings_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
