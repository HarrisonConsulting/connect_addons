"""Post-migrate for connect_elevenlabs 1.6.1:

connect_elevenlabs adds prompt_audio_id / invalid_input_audio_id /
voicemail_audio_id to connect.callflow and greeting_audio_id /
voicemail_audio_id to connect.user.  _sync_audio_fields() fires on
create/write but was never called retroactively — rows that existed before
this extension was installed have all audio M2Os NULL.

This migration bootstraps those records so the audio library reflects the
actual state of every callflow and user, and so that the reference map and
reachability BFS (run by the connect 1.11.0 migration) produce correct results.

Safe to re-run: _sync_one_audio checks for an existing audio before creating,
so re-running produces no duplicates.
"""

import logging

from odoo import api, SUPERUSER_ID

logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    # Callflows: bootstrap any that have text but no audio M2O yet.
    callflows = env['connect.callflow'].sudo().search([
        '|', '|',
        '&', ('prompt_message', '!=', False), ('prompt_audio_id', '=', False),
        '&', ('invalid_input_message', '!=', False), ('invalid_input_audio_id', '=', False),
        '&', ('voicemail_enabled', '=', True),
             '&', ('voicemail_prompt', '!=', False), ('voicemail_audio_id', '=', False),
    ])
    if callflows:
        callflows._sync_audio_fields()
        logger.info('Bootstrapped connect.audio records for %d callflow(s).', len(callflows))

    # Users: bootstrap greeting and voicemail audio.
    users = env['connect.user'].sudo().search([
        '|',
        '&', ('greeting_message', '!=', False), ('greeting_audio_id', '=', False),
        '&', ('voicemail_enabled', '=', True),
             '&', ('voicemail_prompt', '!=', False), ('voicemail_audio_id', '=', False),
    ])
    if users:
        users._sync_audio_fields()
        logger.info('Bootstrapped connect.audio records for %d user(s).', len(users))

    # Rebuild references and reachability now that new audio records exist.
    audios = env['connect.audio'].sudo().search([])
    if audios:
        audios._refresh_references()
        logger.info('Refreshed references for %d audio(s).', len(audios))

    env['connect.audio'].sudo()._refresh_reachability()
    logger.info('Reachability BFS complete.')

    to_promote = env['connect.audio'].sudo().search([
        ('state', '=', 'draft'),
        ('is_reachable', '=', True),
    ])
    if to_promote:
        to_promote.write({'state': 'live'})
        logger.info('Promoted %d bootstrapped audio(s) to live.', len(to_promote))
