# -*- coding: utf-8 -*-

import logging
from markupsafe import escape
from odoo import fields, models, api
from odoo.models import Constraint

logger = logging.getLogger(__name__)


PROVIDERS = [
    ('twilio', 'Twilio'),
    ('elevenlabs', 'ElevenLabs'),
]


class Voice(models.Model):
    _name = 'connect.voice'
    _description = 'Voice'
    _order = 'provider, name'

    name = fields.Char(required=True, help='Display name for this voice.')
    active = fields.Boolean(default=True,
        help='Voices can be archived rather than deleted so existing utterance '
             'rows (which pin voice_id with ondelete=restrict) survive when a '
             'voice disappears upstream. Archived voices are excluded from '
             'default searches but remain available for audit.')
    provider = fields.Selection(PROVIDERS, required=True, default='twilio',
        help='Backend that owns this voice. New providers extend this selection.')
    external_id = fields.Char(required=True,
        help="Provider's voice identifier. For Twilio: alice/man/woman/Polly.* . "
             'For ElevenLabs: the voice_id from the API.')
    language = fields.Char(help='BCP-47 language tag, e.g. en-US.')
    description = fields.Char(
        help='Free-text description of the voice (timbre, style, use-case '
             'hints) as returned by the provider.')
    accent = fields.Char(
        help='Accent label as supplied by the provider '
             '(e.g. "american", "british"). Informational only.')
    age = fields.Char(
        help='Age bucket label as supplied by the provider '
             '(e.g. "young", "middle_aged"). Informational only.')
    gender = fields.Char(
        help='Gender label as supplied by the provider '
             '(e.g. "male", "female"). Informational only.')
    preview_url = fields.Char(help='URL to a sample of this voice. Optional.')
    preview_audio = fields.Html(compute='_compute_preview_audio',
        string='Preview', sanitize=False)

    _unique_provider_external = Constraint(
        'UNIQUE(provider, external_id)',
        'A voice with this provider and external id already exists.',
    )

    @api.depends('preview_url')
    def _compute_preview_audio(self):
        # Escape the src: preview_url is synced from the provider API
        # (ElevenLabs /voices endpoint), which is not controlled by us.
        # An attacker-controlled or malformed URL could break out of the
        # HTML attribute since the Html field renders with sanitize=False.
        for rec in self:
            if rec.preview_url:
                src = escape(rec.preview_url)
                rec.preview_audio = (
                    f'<audio controls><source src="{src}" '
                    'type="audio/mpeg"/></audio>'
                )
            else:
                rec.preview_audio = ''
