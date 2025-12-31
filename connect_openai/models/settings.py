# -*- coding: utf-8 -*-

import logging

from odoo import models, api
from odoo.exceptions import ValidationError

logger = logging.getLogger(__name__)


class ConnectSettingsOpenAI(models.Model):
    """Extend Connect settings to use centralized OpenAI configuration.

    This override replaces the built-in OpenAI configuration in Connect
    with the centralized configuration from the openai_base module.
    """
    _inherit = 'connect.settings'

    @api.model
    def get_openai_client(self):
        """Get OpenAI client using centralized openai_base configuration.

        This method overrides the default implementation to use the
        centralized OpenAI configuration from the openai_base module
        instead of Connect's own openai_api_key and openai_base_url fields.

        Returns:
            OpenAI client instance or False if not configured.
        """
        try:
            return self.env['openai.config'].get_client()
        except ValueError as e:
            logger.warning("OpenAI not configured: %s", e)
            return False
        except Exception as e:
            logger.exception("Failed to get OpenAI client: %s", e)
            return False

    @api.model
    def is_openai_configured(self):
        """Check if OpenAI is configured via openai_base.

        Returns:
            bool: True if OpenAI API key is configured, False otherwise.
        """
        IrConfigParameter = self.env['ir.config_parameter'].sudo()
        api_key = IrConfigParameter.get_param('openai.api_key', '')
        return bool(api_key)

    @api.onchange('transcript_calls')
    def _require_openai_key(self):
        """Override to check centralized OpenAI configuration."""
        if not self.is_openai_configured():
            raise ValidationError(
                "OpenAI is not configured. Please configure it in "
                "Settings > Integrations > OpenAI Integration."
            )
