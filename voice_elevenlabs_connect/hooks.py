# -*- coding: utf-8 -*-
"""
Migration hooks for voice_elevenlabs_connect module.

Handles:
1. Pre-init: Clean up orphaned records from failed installations
2. Post-init: Setup migration wizard if connect_elevenlabs is installed
"""
import logging

_logger = logging.getLogger(__name__)


def pre_init_hook(env):
    """
    Pre-installation setup.

    Checks for legacy connect_elevenlabs module and prepares for migration.
    """
    cr = env.cr
    _logger.info("voice_elevenlabs_connect: Running pre_init_hook")

    # Check if connect_elevenlabs is installed for migration awareness
    cr.execute("""
        SELECT id, state
        FROM ir_module_module
        WHERE name = 'connect_elevenlabs' AND state = 'installed'
    """)
    legacy_module = cr.fetchone()

    if legacy_module:
        _logger.info(
            "voice_elevenlabs_connect: Found installed connect_elevenlabs module. "
            "Migration wizard will be available after installation."
        )

        # Count legacy records
        try:
            for model, table in [
                ('connect.elevenlabs_agent', 'connect_elevenlabs_agent'),
                ('connect.elevenlabs_voice', 'connect_elevenlabs_voice'),
                ('connect.elevenlabs_phone_registration', 'connect_elevenlabs_phone_registration'),
            ]:
                cr.execute(f"SELECT COUNT(*) FROM {table}")
                count = cr.fetchone()[0]
                if count:
                    _logger.info("  - %s: %d records", model, count)
        except Exception as e:
            _logger.debug("Could not count legacy records: %s", e)

    _logger.info("voice_elevenlabs_connect: pre_init_hook completed successfully")


def post_init_hook(env):
    """
    Post-installation setup.

    If connect_elevenlabs is installed, log information about the migration wizard.
    """
    _logger.info("voice_elevenlabs_connect: Running post_init_hook")

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
                "voice_elevenlabs_connect: Legacy connect_elevenlabs data detected:\n"
                "  - Agents: %d\n"
                "  - Voices: %d\n"
                "  - Phone registrations: %d\n"
                "Use the migration wizard (Connect > Settings > ElevenLabs > Migrate from Legacy) "
                "to transfer data to the new framework.",
                agent_count, voice_count, phone_count
            )
        except Exception as e:
            _logger.warning(
                "voice_elevenlabs_connect: Could not count legacy records: %s", e
            )

    _logger.info("voice_elevenlabs_connect: post_init_hook completed successfully")
