# -*- coding: utf-8 -*-
"""Set default system_voice on existing settings before NOT NULL constraint."""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        UPDATE connect_settings
        SET system_voice = 'Polly.Ruth-Generative'
        WHERE system_voice IS NULL
    """)
    if cr.rowcount:
        _logger.info('Set system_voice default on %d settings records', cr.rowcount)
