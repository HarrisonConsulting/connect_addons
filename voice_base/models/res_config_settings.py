# -*- coding: utf-8 -*-
"""
Voice AI Base Settings.

Provides the base res.config.settings extension for Voice AI modules.
Provider-specific modules extend this with their own settings.
"""
import logging

from odoo import models, fields, api

logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    """
    Base Voice AI settings.

    Extended by voice_elevenlabs, voice_elevenlabs_connect, etc.
    """
    _inherit = 'res.config.settings'

    # === General Voice AI Settings ===
    voice_ai_enabled = fields.Boolean(
        string='Enable Voice AI',
        config_parameter='voice_base.enabled',
        default=False,
        help='Enable Voice AI features across all modules'
    )
