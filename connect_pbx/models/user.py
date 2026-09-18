# -*- coding: utf-8 -*-
"""Audio-layer and presence wiring for Connect users.

A user's greeting and voicemail prompts, the shared box their voicemails
land in, and the live telephony presence the phone widget reads are all
declared on top of the Twilio-shaped user record.
"""

from odoo import fields, models

from .audio_referrer_mixin import SELECTABLE_AUDIO_STATES


class User(models.Model):
    _name = 'connect.user'
    _inherit = ['connect.user', 'connect.tts.mixin',
                'connect.audio.referrer.mixin']

    _audio_reference_fields = ('greeting_audio_id', 'voicemail_audio_id')
    _audio_reference_trigger_fields = ('active',)
    _audio_reachability_fields = (
        'active', 'greeting_audio_id', 'voicemail_audio_id', 'voicemail_enabled',
    )

    dnd_enabled = fields.Boolean(string='Do Not Disturb',
        help='When enabled, all incoming calls go directly to voicemail')
    voicemail_audio_id = fields.Many2one('connect.audio', ondelete='set null',
        domain=[('state', 'in', SELECTABLE_AUDIO_STATES)],
        string='Voicemail Prompt Audio',
        help='Audio played when a caller reaches this user\'s voicemail.')
    voicemail_preview = fields.Html(
        related='voicemail_audio_id.latest_utterance_id.preview_audio',
        string='Voicemail Preview', sanitize=False)
    voicemail_box_id = fields.Many2one(
        'connect.voicemail_box', ondelete='set null', string='Voicemail Box',
        help='Shared box for this user\'s personal voicemails. All box members gain access to calls and voicemails routed to this user.')
    greeting_audio_id = fields.Many2one('connect.audio', ondelete='set null',
        domain=[('state', 'in', SELECTABLE_AUDIO_STATES)],
        string='Greeting Audio',
        help='Audio played to callers on first contact, before ringing this '
             'user\'s devices.')
    greeting_preview = fields.Html(
        related='greeting_audio_id.latest_utterance_id.preview_audio',
        string='Greeting Preview', sanitize=False)
    presence_status = fields.Selection([
        ('offline', 'Offline'),
        ('available', 'Available'),
        ('on_call', 'On Call'),
        ('on_hold', 'On Hold'),
    ], string='Presence', default='offline',
        help='Current telephony presence status')
    presence_updated = fields.Datetime(string='Presence Updated',
        help='Last presence status change timestamp')
