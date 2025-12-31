# -*- coding: utf-8 -*-

import json
import logging

from odoo import models, fields

logger = logging.getLogger(__name__)


class ElevenLabsAgentOpenAI(models.Model):
    _inherit = 'elevenlabs.agent'

    # === Custom LLM Configuration (via openai_base) ===
    custom_llm_model_id = fields.Many2one(
        'openai.model',
        string="Custom LLM Model",
        help="Select a model from your configured OpenAI-compatible endpoint. "
             "Configure the endpoint in Settings > Integrations > OpenAI Integration.",
    )
    custom_llm_extra_body = fields.Text(
        string="Extra Body (JSON)",
        help="Additional JSON parameters to send with each request (e.g., {\"user_id\": \"123\"})",
    )

    def _build_custom_llm_config(self):
        """Build custom LLM configuration from openai_base settings.

        Returns a dict compatible with ElevenLabs custom_llm schema:
        {
            'url': 'https://your-endpoint.com/v1/chat/completions',
            'model_id': 'your-model-name',
            'api_key': 'your-api-key',
        }
        """
        IrConfigParameter = self.env['ir.config_parameter'].sudo()

        # Get OpenAI base configuration
        base_url = IrConfigParameter.get_param('openai.base_url', '')
        api_key = IrConfigParameter.get_param('openai.api_key', '')

        if not base_url:
            logger.warning("Custom LLM selected but no OpenAI base URL configured")
            return None

        # Ensure URL ends with /v1/chat/completions for ElevenLabs
        if not base_url.endswith('/'):
            base_url = base_url + '/'
        if not base_url.endswith('v1/'):
            base_url = base_url + 'v1/'
        chat_url = base_url + 'chat/completions'

        config = {
            'url': chat_url,
        }

        # Add model ID if a custom model is selected
        if self.custom_llm_model_id:
            config['model_id'] = self.custom_llm_model_id.model_id

        # Add API key if configured
        if api_key:
            config['api_key'] = api_key

        # Add extra body parameters if configured
        if self.custom_llm_extra_body:
            try:
                extra_body = json.loads(self.custom_llm_extra_body)
                if extra_body:
                    config['extra_body'] = extra_body
            except json.JSONDecodeError:
                logger.warning("Invalid JSON in custom_llm_extra_body, ignoring")

        return config
