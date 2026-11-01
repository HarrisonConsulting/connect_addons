# -*- coding: utf-8 -*-
"""Post-migrate for 1.16.1: park_hold_music_url Char -> connect.audio m2o.

The legacy Char stored a hold-music URL that the park flow passed directly
to Twilio's <Conference waitUrl="...">. This release replaces it with a
Many2one('connect.audio') so hold music participates in the audio library
(reachability, Where-Used, utterance caching) like every other prompt.

Runs in post-migrate so the new m2o column and connect.audio model are
available. For each settings row that still has a non-empty
park_hold_music_url, create a connect.audio row (source=external_url,
static_url=that URL) and wire the m2o; then drop the old column. Rows
whose m2o is already set (e.g. hand-populated on a dev DB) are left alone
so the operator's choice wins.
"""

import logging

from odoo import api, SUPERUSER_ID

logger = logging.getLogger(__name__)


def _column_exists(cr, table, column):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = %s AND column_name = %s
    """, (table, column))
    return bool(cr.fetchone())


def _migrate_park_hold_music(cr, env):
    if not _column_exists(cr, 'connect_settings', 'park_hold_music_url'):
        return
    cr.execute("""
        SELECT id, park_hold_music_url
          FROM connect_settings
         WHERE park_hold_music_url IS NOT NULL
           AND park_hold_music_url != ''
           AND park_hold_music_audio_id IS NULL
    """)
    rows = cr.fetchall()
    logger.info('park_hold_music_url: %d row(s) to convert.', len(rows))

    Audio = env['connect.audio'].sudo()
    for settings_id, url in rows:
        audio = Audio.create({
            'name': 'Park Hold Music',
            'description': 'Migrated from connect_settings.park_hold_music_url',
            'source': 'external_url',
            'static_url': url,
            # Pre-release default: no reason to block operators on a review
            # cycle for a value they already had in production.
            'state': 'live',
        })
        cr.execute(
            'UPDATE connect_settings SET park_hold_music_audio_id = %s '
            'WHERE id = %s',
            (audio.id, settings_id))
        logger.info(
            'connect_settings#%d.park_hold_music_url=%r -> connect.audio#%d.',
            settings_id, url, audio.id)

    cr.execute('ALTER TABLE connect_settings DROP COLUMN park_hold_music_url')
    logger.info('Dropped connect_settings.park_hold_music_url.')


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    _migrate_park_hold_music(cr, env)
    # Raw UPDATE bypassed the referrer mixin — rebuild reference rows and
    # re-walk reachability so the newly-wired hold-music audios appear in
    # Where-Used and are marked reachable.
    audios = env['connect.audio'].sudo().search([])
    if audios:
        audios._refresh_references()
    env['connect.audio'].sudo()._refresh_reachability()
    logger.info('Audio references + reachability refreshed after 1.16.1.')
