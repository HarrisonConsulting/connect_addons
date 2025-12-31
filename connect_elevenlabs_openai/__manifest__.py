# -*- coding: utf-8 -*-

{
    'name': 'Connect ElevenLabs - OpenAI Integration',
    'version': '18.0.1.0.0',
    'category': 'Phone',
    'summary': 'Custom LLM support for ElevenLabs agents via OpenAI-compatible endpoints',
    'description': """
Connect ElevenLabs - OpenAI Integration
=======================================

This bridge module enables custom LLM support for ElevenLabs conversational AI agents
using OpenAI-compatible endpoints configured in the OpenAI Base module.

Features:
- Select custom LLM models from your configured OpenAI-compatible endpoint
- Configure additional request parameters for the custom LLM
- Seamless integration with ElevenLabs agent configuration

Architecture:
- Extends elevenlabs.agent model with custom LLM fields
- Uses openai_base for endpoint configuration and model management
- Clean separation: connect_elevenlabs doesn't depend on openai_base,
  this module bridges the two through inheritance
    """,
    'author': 'Harrison Consulting, LLC',
    'website': 'https://www.harrison.consulting',
    'license': 'OPL-1',
    'depends': ['connect_elevenlabs', 'openai_base'],
    'data': [
        'views/agent_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
