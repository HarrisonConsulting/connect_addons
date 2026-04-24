# -*- coding: utf-8 -*-
"""Post-migrate for 1.17.0: backfill connect.audio.uuid on existing installs.

The 1.17.0 release introduces a required UUID on connect.audio so TwiML and
TwiPy bodies can reference audios via `audio('<uuid>')` without stringly-
typed system keys or raw XML verbs. The XML seed in data/audio.xml pins
stable UUIDs for the eight shipped system audios; everything else gets a
freshly generated uuid4.

We can't rely on the Char field's Python default here because:
  1. The column is added with NOT NULL via ORM's init_models pass, which
     runs BEFORE this hook. On installs with pre-existing connect.audio
     rows, Odoo will populate them with NULL-fallbacks that violate the
     uniqueness constraint, and `fields.Char(default=...)` is only
     consulted during ORM create() — not column add-on-upgrade.
  2. The XML seed's <field name="uuid">…</field> only takes effect when
     the record is (re)created by the loader. Existing rows keep their
     previous uuid (i.e. whatever the pre-hook default wrote), which is
     wrong for the eight system audios — they must match the baked values
     so code that looks them up by UUID works across all installs.

So we do two things, both via direct SQL to bypass the ORM immutability
guard:
  - For each seeded system audio (xml_id → baked UUID), UPDATE to the
    baked value (idempotent: no change when they already match).
  - For every other row whose uuid is NULL or was synthesised by Odoo's
    column-add defaulter, generate a fresh uuid4.

Finally, refresh references + reachability so anything we touched shows
up correctly in Where-Used and the BFS.
"""

import logging
import uuid as uuid_lib

from odoo import api, SUPERUSER_ID

logger = logging.getLogger(__name__)


# xml_id → baked UUID. Must stay in lockstep with connect/data/audio.xml.
# Changing a value here would orphan every TwiML/TwiPy body that already
# references that UUID; the right fix for a typo is to generate a new row
# with a different xml_id, not to rewrite the key.
SYSTEM_AUDIO_UUIDS = {
    'audio_system_transfer':     'a7f3e4b2-8c1d-4e9a-b5f7-2d6a8e4c9b1f',
    'audio_system_connecting':   'c5d2b1e9-3a74-4f68-9c8d-1b7e5f2a9d6c',
    'audio_system_dnd':          'e8b4a7c3-6d59-4e21-8f7a-3c9b2d5e1a8f',
    'audio_error_no_callerid':   '4f2a9b67-1c38-4d5e-a6b2-8e7d3f1c9a54',
    'audio_error_callflow_empty':'b1e5d8f4-2a97-4c63-9b1d-5e8a2f7c4d63',
    'audio_error_call_failed':   '7d3c8e1b-4f96-4a52-b8e7-6c9a1d4f2b83',
    'audio_error_no_extension':  '9a5f6c2d-8b47-4e31-a7c9-2d1b8f4e6c95',
    'audio_error_choice_error':  '2e6b4a1c-7f58-4d92-8a3e-5c9d1b6f8a47',
}


def _column_exists(cr, table, column):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = %s AND column_name = %s
    """, (table, column))
    return bool(cr.fetchone())


def _backfill_system_audio_uuids(env):
    """Pin the eight system audios to their baked UUIDs."""
    cr = env.cr
    fixed = 0
    for xml_id, baked_uuid in SYSTEM_AUDIO_UUIDS.items():
        rec = env.ref(f'connect.{xml_id}', raise_if_not_found=False)
        if not rec:
            logger.info(
                'System audio connect.%s not present in this DB; skipping.',
                xml_id)
            continue
        cr.execute(
            'UPDATE connect_audio SET uuid = %s WHERE id = %s AND '
            '(uuid IS NULL OR uuid <> %s)',
            (baked_uuid, rec.id, baked_uuid))
        if cr.rowcount:
            fixed += cr.rowcount
            logger.info(
                'Pinned connect.audio#%d (%s) uuid -> %s.',
                rec.id, xml_id, baked_uuid)
    logger.info('System audio UUID backfill: %d row(s) updated.', fixed)


def _backfill_missing_uuids(env):
    """Generate uuid4 for any connect.audio row whose uuid is NULL."""
    cr = env.cr
    cr.execute('SELECT id FROM connect_audio WHERE uuid IS NULL')
    rows = cr.fetchall()
    if not rows:
        logger.info('No connect.audio rows missing a uuid — clean install.')
        return
    logger.info(
        '%d connect.audio row(s) missing uuid; generating uuid4 for each.',
        len(rows))
    for (audio_id,) in rows:
        cr.execute(
            'UPDATE connect_audio SET uuid = %s WHERE id = %s',
            (str(uuid_lib.uuid4()), audio_id))
    logger.info('uuid4 backfill complete: %d row(s).', len(rows))


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    if not _column_exists(cr, 'connect_audio', 'uuid'):
        logger.warning(
            'connect_audio.uuid column missing at 1.17.0 post-migrate; '
            'skipping backfill. Odoo init_models should have created it.')
        return
    _backfill_system_audio_uuids(env)
    _backfill_missing_uuids(env)
    # Raw UPDATE bypasses the referrer mixin — rebuild reference rows and
    # re-walk reachability so the newly-wired twiml audios appear in
    # Where-Used and are marked reachable. Cheap at typical scale.
    audios = env['connect.audio'].sudo().search([])
    if audios:
        audios._refresh_references()
    env['connect.audio'].sudo()._refresh_reachability()
    logger.info('Audio references + reachability refreshed after 1.17.0.')
