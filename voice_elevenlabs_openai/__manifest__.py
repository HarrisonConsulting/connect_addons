# -*- coding: utf-8 -*-
{
    'name': 'ElevenLabs Voice - OpenAI Integration',
    'version': '18.0.1.0.1',
    'category': 'Productivity/Voice AI',
    'summary': 'Custom LLM support for ElevenLabs voice agents via OpenAI-compatible endpoints',
    'description': """
ElevenLabs Voice - OpenAI Integration
=====================================

This bridge module enables custom LLM support for ElevenLabs voice agents
using OpenAI-compatible endpoints configured in the OpenAI Base module.

**Features:**

* Select custom LLM models from your configured OpenAI-compatible endpoint
* Configure additional request parameters for the custom LLM
* Seamless integration with voice agent configuration

**Architecture:**

* Extends connect.voice.agent model with custom LLM fields
* Uses openai_base for endpoint configuration and model management
* Overrides _build_custom_llm_config() from voice.agent.mixin to provide
  actual configuration from the OpenAI-compatible endpoint
    """,
    'author': 'Harrison Consulting, LLC',
    'website': 'https://www.harrison.consulting',
    'license': 'OPL-1',
    'depends': [
        'connect_voice',
        'voice_elevenlabs',
        'openai_base',
    ],
    'data': [
        'views/voice_agent_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
