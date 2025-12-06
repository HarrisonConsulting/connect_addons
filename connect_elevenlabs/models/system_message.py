# -*- coding: utf-8 -*-

import logging
from odoo import fields, models, api

logger = logging.getLogger(__name__)

# Default system messages to pre-generate
DEFAULT_SYSTEM_MESSAGES = {
    'system.transfer': 'Transfer',
    'system.connecting': 'Connecting...',
    'error.no_callerid': 'You must configure a default number for caller ID!',
    'error.callflow_empty': 'This callflow has no actions! Goodbye!',
    'error.call_failed': 'Sorry, I could not connect your call. Goodbye!',
    'error.no_extension': 'Extension not configured!',
    'error.choice_error': 'Choice application error, please contact technical support!',
}


class ElevenLabsSystemMessage(models.Model):
    _name = 'connect.elevenlabs_system_message'
    _inherit = 'connect.elevenlabs_file'
    _description = 'Pre-generated System Message Audio'

    message_key = fields.Char(required=True, index=True)

    _sql_constraints = [
        ('unique_message_key', 'UNIQUE(message_key)', 'Message key must be unique')
    ]

    @api.model
    def regenerate_all_system_messages(self):
        """Regenerate all system message audio files."""
        if not self.env['connect.settings'].sudo().get_param('elevenlabs_enabled'):
            logger.warning('ElevenLabs not enabled, skipping system message regeneration')
            return

        for message_key, text in DEFAULT_SYSTEM_MESSAGES.items():
            existing = self.search([('message_key', '=', message_key)], limit=1)
            if existing:
                # Update text if different
                if existing.text != text:
                    existing.text = text
                elif not existing.file:
                    # Force regeneration if file is missing
                    existing._regenerate_file()
            else:
                # Create new system message
                self.create({
                    'message_key': message_key,
                    'text': text,
                })
        logger.info('System messages regenerated successfully')

    @api.model
    def init_system_messages(self):
        """Initialize system messages on module install/update."""
        # Only create if ElevenLabs is enabled
        if not self.env['connect.settings'].sudo().get_param('elevenlabs_enabled'):
            return

        for message_key, text in DEFAULT_SYSTEM_MESSAGES.items():
            existing = self.search([('message_key', '=', message_key)], limit=1)
            if not existing:
                try:
                    self.create({
                        'message_key': message_key,
                        'text': text,
                    })
                except Exception as e:
                    logger.error('Failed to create system message %s: %s', message_key, e)
