# -*- coding: utf-8 -*-
"""Wizard for archiving an audio that still has active references.

Flow:
  1. User clicks Archive on a connect.audio with active_reference_count > 0.
  2. audio.action_archive_audio() opens this wizard pre-populated with one
     line per active reference.
  3. For each line the user picks an action: leave (block), clear (blank
     the m2o), or swap (point the m2o at another audio).
  4. Apply performs the writes on each referring record, re-refreshes the
     audio's references, and — if no active references remain — archives.

Keeps the user from stealth-breaking a live call flow: archiving something
still referenced by a running campaign would silently route the campaign
through a null audio (Twilio <Say> fallback or worse). The wizard makes
the operator confront each reference explicitly.
"""

import logging

from odoo import fields, models, api
from odoo.exceptions import UserError
from odoo.addons.connect_hc_core.models.audio_referrer_mixin import (
    SELECTABLE_AUDIO_STATES,
)

logger = logging.getLogger(__name__)


class AudioArchiveWizard(models.TransientModel):
    _name = 'connect.audio.archive.wizard'
    _description = 'Archive Audio Wizard'

    audio_id = fields.Many2one('connect.audio', required=True, ondelete='cascade',
        readonly=True,
        help='The audio being archived. Pre-populated from the button action '
             'on the audio form.')
    line_ids = fields.One2many('connect.audio.archive.wizard.line', 'wizard_id',
        string='Active References',
        help='One line per active reference that must be resolved before the '
             'audio can transition to archived.')
    remaining_active_count = fields.Integer(
        compute='_compute_remaining_active',
        help='How many references would still be active after the chosen '
             'actions are applied. Must reach zero before archiving.')

    @api.depends('line_ids.action')
    def _compute_remaining_active(self):
        for wiz in self:
            wiz.remaining_active_count = sum(
                1 for line in wiz.line_ids if line.action == 'leave')

    def action_apply_and_archive(self):
        """Apply each line's chosen action, then archive the audio.

        Raises UserError if any line still says 'leave' — the user would be
        archiving something actively routed to. Makes the mistake impossible
        to commit accidentally.
        """
        self.ensure_one()
        blocking = self.line_ids.filtered(
            lambda l: l.action == 'leave' and l.reference_id.is_active)
        if blocking:
            names = ', '.join(blocking.mapped('reference_id.referrer_name'))
            raise UserError(
                f'These references would still route live calls through '
                f'{self.audio_id.name!r}: {names}. Choose Clear or Swap for '
                f'each before archiving.'
            )
        for line in self.line_ids:
            line._apply_action()
        # Force a synchronous refresh so the remaining_active check below
        # sees ground truth, not a queued-but-unfired job.
        self.audio_id._refresh_references()
        still_active = self.audio_id.active_reference_count
        if still_active:
            raise UserError(
                f'After applying your choices, {still_active} reference(s) '
                f'are still active. Refresh and try again.'
            )
        self.audio_id.state = 'archived'
        return {'type': 'ir.actions.act_window_close'}


class AudioArchiveWizardLine(models.TransientModel):
    _name = 'connect.audio.archive.wizard.line'
    _description = 'Archive Audio Wizard Line'

    wizard_id = fields.Many2one('connect.audio.archive.wizard', required=True,
        ondelete='cascade',
        help='Parent wizard owning this line.')
    reference_id = fields.Many2one('connect.audio.reference', required=True,
        ondelete='cascade', readonly=True,
        help='The audio reference row this line proposes to resolve.')
    referrer_model_label = fields.Char(
        related='reference_id.referrer_model_label', readonly=True,
        help='Human-readable model of the referring record.')
    referrer_name = fields.Char(
        related='reference_id.referrer_name', readonly=True,
        help='display_name of the referring record at wizard-open time.')
    field_name = fields.Char(related='reference_id.field_name', readonly=True,
        help='Many2one field on the referrer that points at this audio.')
    is_active = fields.Boolean(related='reference_id.is_active', readonly=True,
        help='True when the referring record would route a live call here.')
    is_reachable = fields.Boolean(related='reference_id.is_reachable',
        readonly=True,
        help='True when the referrer is reachable from a routing entry point.')
    action = fields.Selection([
        ('leave', 'Leave (blocks archive)'),
        ('clear', 'Clear field'),
        ('swap', 'Swap to another audio'),
    ], required=True, default='leave',
        help='Resolution for this reference: leave (blocks archive), clear '
             '(blank the m2o), or swap (point the m2o at a replacement).')
    replacement_audio_id = fields.Many2one('connect.audio',
        string='Replacement',
        domain="[('id', '!=', wizard_id.audio_id), ('state', 'in', %s)]" % (
            repr(SELECTABLE_AUDIO_STATES),
        ),
        help='Required when action is Swap. Cannot be an archived audio.')

    @api.constrains('action', 'replacement_audio_id')
    def _check_swap_has_replacement(self):
        for line in self:
            if line.action == 'swap' and not line.replacement_audio_id:
                raise UserError(
                    f'Reference {line.referrer_name!r}: Swap action requires '
                    f'a Replacement audio.')

    def _apply_action(self):
        """Write the chosen action through to the referring record."""
        self.ensure_one()
        ref = self.reference_id
        if self.action == 'leave':
            return
        referrer = self.env[ref.referrer_model].sudo().browse(ref.referrer_res_id)
        if not referrer.exists():
            # Record vanished between wizard open and apply — nothing to do.
            return
        if self.action == 'clear':
            referrer.write({ref.field_name: False})
        elif self.action == 'swap':
            referrer.write({ref.field_name: self.replacement_audio_id.id})
