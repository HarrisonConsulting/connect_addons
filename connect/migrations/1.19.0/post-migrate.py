# -*- coding: utf-8 -*-
"""Post-migrate for 1.19.0: normalise draft connect.audio rows with referrers.

1.19.0 introduces a server-side invariant that draft and archived audios
are not selectable as referrer targets (see
connect.audio.referrer.mixin._check_audio_selectable). Before this release
an operator could wire a draft audio into a live callflow/user/callout
and BFS would mark it reachable while the audio's state still said draft
— the contradiction the lifecycle refactor eliminates.

For any pre-existing draft audio that currently has a referrer, promote
it to reviewed. The audio model's reviewed ↔ live reconciliation will
immediately flip it to live if any active reference exists, so after this
step the invariant holds (no draft + reference_count > 0 rows remain).

Alternative considered: leave the row draft and raise on the next referrer
write. Rejected — that would turn every post-upgrade write on a pre-
existing callflow into a ValidationError with no operator-visible signal
beforehand, which is a poor upgrade experience. Auto-promotion is the
least-surprise normalisation.

Finally refresh references + reachability so the graph reflects the
promoted state. Cheap at typical scale.
"""

import logging

from odoo import api, SUPERUSER_ID

logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Audio = env['connect.audio'].sudo()
    # reference_count is a stored compute but may not yet reflect the
    # current ref graph if an earlier migration bypassed the mixin. Rebuild
    # references first so we promote based on current reality, not stale
    # counts.
    all_audios = Audio.search([])
    if all_audios:
        all_audios._refresh_references()

    drafts_with_refs = Audio.search([
        ('state', '=', 'draft'),
        ('reference_count', '>', 0),
    ])
    if drafts_with_refs:
        logger.info(
            '1.19.0: promoting %d draft audio(s) with existing referrers '
            'to reviewed (auto-flip to live applies where active).',
            len(drafts_with_refs))
        drafts_with_refs.write({'state': 'reviewed'})
        # _reconcile_state_with_references runs per-audio inside
        # _refresh_references; triggering it explicitly here flips the
        # just-promoted rows to live when they have an active reference.
        drafts_with_refs._refresh_references()
    else:
        logger.info('1.19.0: no draft audios with pre-existing referrers.')

    Audio._refresh_reachability()
    logger.info('1.19.0: references + reachability refreshed.')
