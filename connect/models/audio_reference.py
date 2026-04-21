# -*- coding: utf-8 -*-
"""Stored map of which records use which connect.audio.

One row per (audio, referrer). Populated by connect.audio._refresh_references()
which walks the _audio_referrers() hook. Stored so the Audio Overview can
aggregate and filter cheaply. Refresh is triggered at write-time via the
connect.audio.referrer.mixin; operators can force a global rebuild from the
Overview's "Verify Reachability" header button if they suspect drift.

The `is_active` flag separates "record exists" from "would actually play on a
live call" — critical for the connect.audio state machine (reviewed ↔ live).
The `is_reachable` flag separates "referrer exists" from "a call could
actually land here given current DID/campaign routing".
"""

import logging

from odoo import fields, models, api

logger = logging.getLogger(__name__)


class AudioReference(models.Model):
    _name = 'connect.audio.reference'
    _description = 'Audio Reference'
    _order = 'audio_id, referrer_model, referrer_res_id'
    _rec_name = 'referrer_name'

    audio_id = fields.Many2one('connect.audio', required=True, ondelete='cascade',
        index=True)
    referrer_model = fields.Char(required=True, index=True,
        help='Technical model name of the record that references this audio '
             '(e.g. connect.callout).')
    referrer_model_label = fields.Char(
        help='Human-readable model name resolved from ir.model at refresh time.')
    referrer_res_id = fields.Integer(required=True,
        help='Database id of the referring record.')
    referrer_name = fields.Char(
        help='display_name of the referring record at refresh time.')
    field_name = fields.Char(required=True,
        help='The Many2one field on the referrer that points here.')
    is_active = fields.Boolean(default=False, index=True,
        help='True when the referring record is itself in a state that would '
             'route a live call through this audio. Model-specific predicate.')
    inactive_reason = fields.Char(
        help='Why is_active is False (e.g. "callout in draft", "user archived"). '
             'Blank when is_active=True.')
    is_reachable = fields.Boolean(default=False, index=True,
        help='True when this referrer is reachable from a routing entry point '
             '(inbound DID, running outbound campaign) via the may-reach BFS. '
             'Orthogonal to is_active: a reference can be active-state but '
             'unreachable (dead prompt) or inactive-state but theoretically '
             'reachable (campaign paused but still wired).')

    def open_referrer(self):
        """Server-action-ready: jump to the referring record in a fresh form."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': self.referrer_model,
            'res_id': self.referrer_res_id,
            'view_mode': 'form',
            'target': 'current',
        }

    @api.model
    def action_verify_reachability(self):
        """Overview toolbar entry point: delegate to the global refresh on
        connect.audio and surface its notification toast. Bound to the list
        view's <header> button so operators can trigger a fresh BFS without
        selecting any row."""
        return self.env['connect.audio'].action_refresh_reachability()
