# -*- coding: utf-8 -*-
"""Add system_voice column with default before ORM applies NOT NULL constraint."""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        ALTER TABLE connect_settings
        ADD COLUMN IF NOT EXISTS system_voice VARCHAR
        DEFAULT 'Polly.Ruth-Generative'
    """)
    _logger.info('Added system_voice column with default')
