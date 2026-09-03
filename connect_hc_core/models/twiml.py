# -*- coding: utf-8 -*-
"""Audio-layer wiring for TwiML apps.

A TwiML body cites audios by UUID through the ``audio()`` helper. The scan
that turns those citations into a stored reference set, and the TwiML's
participation in the audio reachability graph, belong to the audio layer.
"""

import logging

from odoo import fields, models, api

from odoo.addons.connect.models.twiml import _AUDIO_CALL_RE

logger = logging.getLogger(__name__)


class TwiML(models.Model):
    _name = 'connect.twiml'
    _inherit = ['connect.twiml', 'connect.audio.referrer.mixin']

    # M2m shape — the body can reference any number of audios. The mixin
    # default walks m2o fields; _collect_referenced_audio_ids is overridden
    # below to pull from this M2m instead.
    _audio_reference_fields = ('referenced_audio_ids',)
    # Body + code_type edits can change which audios are referenced without
    # appearing directly as an _audio_reference_fields write (the M2m is a
    # stored compute, not a user-written field). Trigger ref-refresh on any
    # of them so Where-Used and the state machine keep up. `exten=None`
    # means the TwiML is orphaned (not wired to any extension) — the
    # Where-Used row should flip inactive.
    _audio_reference_trigger_fields = ('twiml', 'twipy', 'code_type', 'exten')
    # Reachability: any change to the body, type, or exten wiring flips
    # which audios the twiml could play on a live call.
    _audio_reachability_fields = ('twiml', 'twipy', 'code_type', 'exten')

    referenced_audio_ids = fields.Many2many(
        'connect.audio',
        compute='_compute_referenced_audio_ids',
        store=True,
        context={'active_test': False},
        string='Referenced Audio',
        help='Audios invoked by this TwiML via audio("<uuid>") tokens. '
             'Populated by scanning the template source on save; keeps the '
             'routing graph and Where-Used reflecting reality. Archived '
             'audios stay in the set (active_test off) — a body citing a '
             'retired audio is exactly what the "References Archived Audio" '
             'filter has to find. For code_type=model_method the set is '
             'always empty — model.method dispatch is ungoverned by the '
             'audio library.',
    )

    @api.depends('twiml', 'twipy', 'code_type')
    def _compute_referenced_audio_ids(self):
        Audio = self.env['connect.audio'].sudo()
        for rec in self:
            if rec.code_type == 'twiml':
                body = rec.twiml or ''
            elif rec.code_type == 'twipy':
                body = rec.twipy or ''
            else:
                # model_method — ungoverned by the audio library. Whatever
                # the dispatched Python does with connect.audio is its own
                # business; we don't try to static-analyse arbitrary method
                # bodies.
                rec.referenced_audio_ids = [(5, 0, 0)]
                continue
            # Lowercase at the scanner boundary — connect.audio.uuid is
            # stored lowercase (uuid4 default + DB unique index is case-
            # sensitive). Bodies may use uppercase hex from copy-paste.
            uuids = {u.lower() for u in _AUDIO_CALL_RE.findall(body)}
            if not uuids:
                rec.referenced_audio_ids = [(5, 0, 0)]
                continue
            # active_test=False — archived audios (active=False) must be
            # included so the 'References Archived Audio' search filter
            # (domain on referenced_audio_ids.state) can surface twimls
            # citing retired audios. Mirrors the render-time helper.
            audios = Audio.with_context(active_test=False).search(
                [('uuid', 'in', list(uuids))])
            # Warn on misses so operators see dropped references in logs —
            # the helper will also log at render time, but catching them at
            # save time gives a faster feedback loop.
            hits = set(audios.mapped('uuid'))
            missing = uuids - hits
            if missing:
                logger.warning(
                    'connect.twiml#%s body references unknown audio uuids: %s',
                    rec.id, sorted(missing))
            rec.referenced_audio_ids = [(6, 0, audios.ids)]

    def _collect_referenced_audio_ids(self):
        """Override mixin default — our reference field is an M2m, not an m2o.

        The mixin's base implementation treats each entry in
        _audio_reference_fields as a Many2one. TwiML bodies can cite any
        number of audios, so the M2m is the natural shape — but we have
        to surface its ids in the set the mixin uses to drive reference
        refresh and reachability recomputes.
        """
        ids = set()
        for rec in self:
            ids.update(rec.referenced_audio_ids.ids)
        return ids
