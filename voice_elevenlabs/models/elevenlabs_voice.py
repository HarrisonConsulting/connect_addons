# -*- coding: utf-8 -*-
"""
ElevenLabs Voice Extensions

This module extends the base voice.voice model with ElevenLabs-specific
functionality and helpers.
"""
import logging
from odoo import models, api

logger = logging.getLogger(__name__)


class VoiceVoiceElevenLabs(models.Model):
    """
    Extension of voice.voice for ElevenLabs-specific functionality.

    The actual voice syncing is handled by the voice.provider.elevenlabs
    model's sync_voices() method. This extension provides helper methods
    for ElevenLabs-specific voice operations.
    """
    _inherit = 'voice.voice'

    @api.model
    def sync_elevenlabs_voices(self, provider_id=None):
        """
        Helper method to sync voices from ElevenLabs provider(s).

        Args:
            provider_id (int, optional): Specific provider ID to sync.
                                        If not provided, syncs all ElevenLabs providers.

        Returns:
            dict: Aggregated sync results
        """
        domain = [('provider_type', '=', 'elevenlabs')]
        if provider_id:
            domain.append(('id', '=', provider_id))

        providers = self.env['voice.provider.elevenlabs'].search(domain)

        if not providers:
            logger.warning("No ElevenLabs providers found to sync")
            return {'synced': 0, 'created': 0, 'updated': 0}

        total_synced = 0
        total_created = 0
        total_updated = 0

        for provider in providers:
            result = provider.sync_voices()
            total_synced += result.get('synced', 0)
            total_created += result.get('created', 0)
            total_updated += result.get('updated', 0)

        logger.info(
            "Total ElevenLabs voice sync: %d synced (%d created, %d updated)",
            total_synced, total_created, total_updated
        )

        return {
            'synced': total_synced,
            'created': total_created,
            'updated': total_updated,
        }

    def action_test_elevenlabs_voice(self):
        """
        Test this voice with ElevenLabs TTS.

        This would generate a sample audio using the voice.
        """
        self.ensure_one()

        if self.voice_provider_id.provider_type != 'elevenlabs':
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Not ElevenLabs Voice',
                    'message': 'This voice is not from an ElevenLabs provider',
                    'type': 'warning',
                    'sticky': False,
                }
            }

        # Test TTS generation uses the preview URL when available
        # Custom text TTS testing can be added as a future enhancement
        if self.preview_url:
            return self.action_preview_voice()
        else:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Test Voice',
                    'message': 'Voice testing not yet implemented',
                    'type': 'info',
                    'sticky': False,
                }
            }
