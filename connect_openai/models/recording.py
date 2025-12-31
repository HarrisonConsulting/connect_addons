# -*- coding: utf-8 -*-

import logging

from odoo import models
from odoo.exceptions import ValidationError

logger = logging.getLogger(__name__)


class ConnectRecordingOpenAI(models.Model):
    """Extend Connect recording to use centralized OpenAI configuration."""
    _inherit = 'connect.recording'

    def get_transcript(self, fail_silently=False):
        """Override to check centralized OpenAI configuration.

        This method overrides the default implementation to check for
        OpenAI configuration via openai_base instead of Connect's own
        openai_api_key field.
        """
        self.ensure_one()

        # Check centralized OpenAI configuration
        if not self.env['connect.settings'].is_openai_configured():
            if fail_silently:
                logger.warning('OpenAI is not configured! Transcription will not be available.')
                return False
            else:
                raise ValidationError(
                    'OpenAI is not configured. Please configure it in '
                    'Settings > Integrations > OpenAI Integration.'
                )

        summary_prompt = self.env['connect.settings'].get_param('summary_prompt')
        if not self.media_url:
            raise ValidationError('Recording is not available yet!')

        # Pass None for openai_api_key since we use get_openai_client() internally
        self.transcribe_recording(None, summary_prompt)
