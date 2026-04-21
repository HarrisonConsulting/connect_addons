# -*- coding: utf-8 -*-
"""Mixin for models that reference connect.audio via Many2one.

Any model with a field like `prompt_audio_id = fields.Many2one('connect.audio')`
should inherit this mixin. The mixin catches create/write at the source and
enqueues an async refresh of the audio reference map so the Where Used tab
and the state machine stay accurate without a polling cron.

Declare on the inheriting model:

    _audio_reference_fields = ('prompt_audio_id', 'invalid_input_audio_id')
    _audio_reference_trigger_fields = ('active',)  # or ('status',)

`_audio_reference_fields` lists the m2o fields that point at connect.audio.
`_audio_reference_trigger_fields` lists additional fields whose changes flip
the referrer's `is_active` verdict (e.g. a callout.status change from 'draft'
to 'running' doesn't touch prompt_audio_id, but a live call now routes
through the audio — we still need to refresh).

Async dispatch uses queue_job.with_delay() when available; falls back to
inline refresh so the mixin works on installations without queue_job.
Errors are logged, never raised — a bookkeeping miss must not roll back
the user's actual save.
"""

import logging

from odoo import api, models

logger = logging.getLogger(__name__)


class AudioReferrerMixin(models.AbstractModel):
    _name = 'connect.audio.referrer.mixin'
    _description = 'Audio Referrer Mixin'

    #: Tuple of Many2one field names on this model that point at connect.audio.
    _audio_reference_fields = ()
    #: Tuple of scalar field names whose changes affect the is_active verdict
    #: of references to a connect.audio from this model, but don't themselves
    #: change which audio is referenced.
    _audio_reference_trigger_fields = ()
    #: Tuple of field names whose changes could flip which audios are reachable
    #: from the routing graph (DID destination, callflow ring users, etc.).
    #: When any of these change we queue a global reachability recompute.
    #: Models that only participate in routing (no audio m2o) use this alone
    #: with an empty _audio_reference_fields.
    _audio_reachability_fields = ()

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._refresh_audio_references_async()
        if records._audio_reachability_fields or records._audio_reference_fields:
            records.env['connect.audio']._refresh_reachability_async()
        return records

    def write(self, vals):
        # Capture the audios referenced BEFORE the write — any that get
        # unlinked by the write still need their reference rows cleaned up.
        old_ids = self._collect_referenced_audio_ids()
        result = super().write(vals)
        ref_touched = (set(self._audio_reference_fields)
                       | set(self._audio_reference_trigger_fields)) & vals.keys()
        reach_touched = set(self._audio_reachability_fields) & vals.keys()
        if ref_touched:
            new_ids = self._collect_referenced_audio_ids()
            affected = old_ids | new_ids
            if affected:
                self.env['connect.audio'].sudo().browse(
                    list(affected)).exists()._refresh_references_async()
        if ref_touched or reach_touched:
            # Reachability is a global property of the routing graph —
            # any edge change means the whole graph must be re-walked.
            self.env['connect.audio']._refresh_reachability_async()
        return result

    def unlink(self):
        # Capture refs BEFORE unlink — after deletion we can't traverse self.
        affected = self._collect_referenced_audio_ids()
        result = super().unlink()
        if affected:
            self.env['connect.audio'].sudo().browse(
                list(affected)).exists()._refresh_references_async()
        if self._audio_reachability_fields or self._audio_reference_fields:
            self.env['connect.audio']._refresh_reachability_async()
        return result

    def _collect_referenced_audio_ids(self):
        """Return the set of connect.audio ids currently referenced by self."""
        ids = set()
        for rec in self:
            for field_name in self._audio_reference_fields:
                audio = rec[field_name]
                if audio:
                    ids.add(audio.id)
        return ids

    def _refresh_audio_references_async(self):
        """Queue refresh for audios referenced by self."""
        if not self._audio_reference_fields:
            return
        audio_ids = self._collect_referenced_audio_ids()
        if not audio_ids:
            return
        self.env['connect.audio'].sudo().browse(
            list(audio_ids)).exists()._refresh_references_async()
