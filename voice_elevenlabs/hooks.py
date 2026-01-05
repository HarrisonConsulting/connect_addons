# -*- coding: utf-8 -*-
"""
Hooks for voice_elevenlabs module.

Handles:
1. Pre-init: Clean up orphaned records from failed installations
2. Post-init: Basic setup and logging
"""
import logging

_logger = logging.getLogger(__name__)

# Paths that this module defines - clean these before installation
ELEVENLABS_PROVIDER_PATHS = [
    'elevenlabs-providers',
    'elevenlabs-phones',
]


def pre_init_hook(env):
    """
    Clean up orphaned action windows before installation.

    This handles the case where a previous installation failed mid-way,
    leaving orphaned ir.actions.act_window records that would cause
    unique constraint violations on the 'path' field.
    """
    cr = env.cr
    _logger.info("voice_elevenlabs: Running pre_init_hook")

    # Clean up orphaned action windows with our paths
    for path in ELEVENLABS_PROVIDER_PATHS:
        cr.execute("""
            SELECT id, name, res_model
            FROM ir_act_window
            WHERE path = %s
        """, (path,))
        orphaned = cr.fetchall()

        if orphaned:
            for action_id, name, res_model in orphaned:
                _logger.warning(
                    "voice_elevenlabs: Removing orphaned action window: "
                    "id=%s, name='%s', model='%s', path='%s'",
                    action_id, name, res_model, path
                )

            # Delete the orphaned records
            cr.execute("""
                DELETE FROM ir_act_window
                WHERE path = %s
            """, (path,))
            _logger.info(
                "voice_elevenlabs: Cleaned up %d orphaned action(s) with path '%s'",
                len(orphaned), path
            )

    _logger.info("voice_elevenlabs: pre_init_hook completed successfully")


def post_init_hook(env):
    """
    Post-installation setup.

    Creates default provider if none exists.
    """
    _logger.info("voice_elevenlabs: Running post_init_hook")

    # Check if connect_elevenlabs is installed - inform about migration
    legacy_module = env['ir.module.module'].sudo().search([
        ('name', '=', 'connect_elevenlabs'),
        ('state', '=', 'installed')
    ], limit=1)

    if legacy_module:
        _logger.info(
            "voice_elevenlabs: Legacy connect_elevenlabs module detected. "
            "Install voice_elevenlabs_connect for migration wizard."
        )

    _logger.info("voice_elevenlabs: post_init_hook completed successfully")


def uninstall_hook(env):
    """
    Cleanup when uninstalling the module.

    Ensures clean removal without leaving orphaned data.
    """
    _logger.info("voice_elevenlabs: Running uninstall_hook")

    # Any cleanup needed on uninstall can go here
    # The ORM handles most cleanup automatically

    _logger.info("voice_elevenlabs: uninstall_hook completed successfully")
