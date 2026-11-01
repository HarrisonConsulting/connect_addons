# -*- coding: utf-8 -*-
"""Post-migrate for 1.18.0: backfill uuids on the two fallback system audios.

1.18.0 introduces the TwiML audio() helper's fallback routing — two new
system audios (fallback.archived, fallback.unresolved) whose UUIDs must
match the baked values in connect/data/audio.xml so the helper's
system_key lookup resolves consistently across all installs.

We do NOT re-backfill the 1.17.0 system audio set here; that migration
already pinned those UUIDs. This hook covers only the two new rows so
upgrades from 1.17.x → 1.18.0 end up with matching UUIDs even when the
rows pre-existed (hand-created by an operator who beat the migration to
it, for example — unlikely but cheap to handle idempotently).

Pattern mirrors migrations/1.17.0/post-migrate.py — raw SQL UPDATE that
bypasses the ORM immutability guard, idempotent via the `WHERE uuid <> %s`
clause. A system_key collision (some operator hand-created a row with the
same key before the upgrade) is logged and skipped rather than raised —
the 1.17.0 migration's philosophy was "never fail an upgrade over
bookkeeping" and we keep it here.
"""

import logging

from odoo import api, SUPERUSER_ID

logger = logging.getLogger(__name__)


# xml_id → baked UUID. Must stay in lockstep with connect/data/audio.xml.
# Scope is Phase-2-only: re-pinning the 1.17.0 set here would duplicate
# work the 1.17.0 hook already did.
FALLBACK_AUDIO_UUIDS = {
    'audio_fallback_archived':   '32e25059-4874-4ffd-be2a-1ab914e75271',
    'audio_fallback_unresolved': '9d9be4e7-a64a-4649-afd3-3665f43fb9a9',
}


def _column_exists(cr, table, column):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = %s AND column_name = %s
    """, (table, column))
    return bool(cr.fetchone())


def _backfill_fallback_audio_uuids(env):
    """Pin the two fallback audios to their baked UUIDs."""
    cr = env.cr
    fixed = 0
    for xml_id, baked_uuid in FALLBACK_AUDIO_UUIDS.items():
        rec = env.ref(f'connect.{xml_id}', raise_if_not_found=False)
        if not rec:
            logger.info(
                'Fallback audio connect.%s not present in this DB; skipping.',
                xml_id)
            continue
        # Guard against the unlikely case: an operator hand-created a row
        # with the same system_key and a different uuid, and the XML loader
        # is about to import the baked uuid into a different row. The unique
        # constraint would bounce the UPDATE; log and move on rather than
        # halt the upgrade.
        try:
            with cr.savepoint():
                cr.execute(
                    'UPDATE connect_audio SET uuid = %s WHERE id = %s AND '
                    '(uuid IS NULL OR uuid <> %s)',
                    (baked_uuid, rec.id, baked_uuid))
                if cr.rowcount:
                    fixed += cr.rowcount
                    logger.info(
                        'Pinned connect.audio#%d (%s) uuid -> %s.',
                        rec.id, xml_id, baked_uuid)
        except Exception as e:
            logger.warning(
                'Could not pin uuid for connect.%s (%s); likely a manual row '
                'holds the baked uuid. Manual cleanup required: %s',
                xml_id, baked_uuid, e)
    logger.info('Fallback audio UUID backfill: %d row(s) updated.', fixed)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    if not _column_exists(cr, 'connect_audio', 'uuid'):
        logger.warning(
            'connect_audio.uuid column missing at 1.18.0 post-migrate; '
            'skipping fallback backfill.')
        return
    _backfill_fallback_audio_uuids(env)
    # Raw UPDATE bypasses the referrer mixin — rebuild reference rows and
    # re-walk reachability so the two new audios appear correctly wherever
    # Where-Used / BFS touches them. Scope is small (two rows) but the
    # reachability pass is global; cheap at typical scale.
    audios = env['connect.audio'].sudo().search(
        [('system_key', 'in', list(
            {'fallback.archived', 'fallback.unresolved'}))])
    if audios:
        audios._refresh_references()
    env['connect.audio'].sudo()._refresh_reachability()
    logger.info('Fallback audio references + reachability refreshed after 1.18.0.')
