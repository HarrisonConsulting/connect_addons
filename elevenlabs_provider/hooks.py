# -*- coding: utf-8 -*-
"""
Migration hooks for elevenlabs_provider module.

Handles:
1. Pre-init: Clean up orphaned records from failed installations
2. Post-init: Setup migration wizard if connect_elevenlabs is installed
"""
import logging

_logger = logging.getLogger(__name__)

# Paths that this module defines - clean these before installation
ELEVENLABS_PROVIDER_PATHS = [
    'elevenlabs-providers',
    'elevenlabs-phones',
    'elevenlabs-migration',
]


def pre_init_hook(env):
    """
    Clean up orphaned action windows before installation.

    This handles the case where a previous installation failed mid-way,
    leaving orphaned ir.actions.act_window records that would cause
    unique constraint violations on the 'path' field.

    Also handles cleanup when migrating from connect_elevenlabs.
    """
    cr = env.cr
    _logger.info("elevenlabs_provider: Running pre_init_hook")

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
                    "elevenlabs_provider: Removing orphaned action window: "
                    "id=%s, name='%s', model='%s', path='%s'",
                    action_id, name, res_model, path
                )

            # Delete the orphaned records
            cr.execute("""
                DELETE FROM ir_act_window
                WHERE path = %s
            """, (path,))
            _logger.info(
                "elevenlabs_provider: Cleaned up %d orphaned action(s) with path '%s'",
                len(orphaned), path
            )

    # Check if connect_elevenlabs is installed for migration awareness
    cr.execute("""
        SELECT id, state
        FROM ir_module_module
        WHERE name = 'connect_elevenlabs' AND state = 'installed'
    """)
    legacy_module = cr.fetchone()

    if legacy_module:
        _logger.info(
            "elevenlabs_provider: Found installed connect_elevenlabs module. "
            "Migration wizard will be available after installation."
        )

    _logger.info("elevenlabs_provider: pre_init_hook completed successfully")


def post_init_hook(env):
    """
    Post-installation setup.

    If connect_elevenlabs is installed, log information about the migration wizard.
    """
    _logger.info("elevenlabs_provider: Running post_init_hook")

    # Check if connect_elevenlabs is installed
    legacy_module = env['ir.module.module'].sudo().search([
        ('name', '=', 'connect_elevenlabs'),
        ('state', '=', 'installed')
    ], limit=1)

    if legacy_module:
        # Count legacy records for migration awareness
        try:
            agent_count = env['connect.elevenlabs_agent'].sudo().search_count([])
            voice_count = env['connect.elevenlabs_voice'].sudo().search_count([])
            phone_count = env['connect.elevenlabs_phone_registration'].sudo().search_count([])

            _logger.info(
                "elevenlabs_provider: Legacy connect_elevenlabs data detected:\n"
                "  - Agents: %d\n"
                "  - Voices: %d\n"
                "  - Phone registrations: %d\n"
                "Use the migration wizard to transfer data to the new framework.",
                agent_count, voice_count, phone_count
            )
        except Exception as e:
            _logger.warning(
                "elevenlabs_provider: Could not count legacy records: %s", e
            )

    _logger.info("elevenlabs_provider: post_init_hook completed successfully")


def uninstall_hook(env):
    """
    Cleanup when uninstalling the module.

    Ensures clean removal without leaving orphaned data.
    """
    _logger.info("elevenlabs_provider: Running uninstall_hook")

    # Any cleanup needed on uninstall can go here
    # The ORM handles most cleanup automatically

    _logger.info("elevenlabs_provider: uninstall_hook completed successfully")
