# -*- coding: utf-8 -*-
"""Audio-layer wiring for TwiML apps.

A TwiML body cites audios by UUID through the ``audio()`` helper. The scan
that turns those citations into a stored reference set, and the TwiML's
participation in the audio reachability graph, belong to the audio layer.
"""

import logging
import re

from odoo import fields, models, api

logger = logging.getLogger(__name__)

_AUDIO_CALL_RE = re.compile(
    r'audio\(\s*[\'"]([0-9a-fA-F-]{36})[\'"]\s*\)')


class TwiML(models.Model):
    _name = 'connect.twilio.twiml'
    _inherit = ['connect.twilio.twiml', 'connect.audio.referrer.mixin']

    _audio_reference_fields = ('referenced_audio_ids',)
    _audio_reference_trigger_fields = ('twiml', 'twipy', 'code_type', 'exten')
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
                rec.referenced_audio_ids = [(5, 0, 0)]
                continue
            uuids = {u.lower() for u in _AUDIO_CALL_RE.findall(body)}
            if not uuids:
                rec.referenced_audio_ids = [(5, 0, 0)]
                continue
            audios = Audio.with_context(active_test=False).search(
                [('uuid', 'in', list(uuids))])
            hits = set(audios.mapped('uuid'))
            missing = uuids - hits
            if missing:
                logger.warning(
                    'connect.twilio.twiml#%s body references unknown audio uuids: %s',
                    rec.id, sorted(missing))
            rec.referenced_audio_ids = [(6, 0, audios.ids)]

    def _collect_referenced_audio_ids(self):
        """Override mixin default — our reference field is an M2m, not an m2o."""
        ids = set()
        for rec in self:
            ids.update(rec.referenced_audio_ids.ids)
        return ids
