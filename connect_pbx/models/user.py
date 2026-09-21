# -*- coding: utf-8 -*-
"""Audio-layer and presence wiring for Connect users.

A user's greeting and voicemail prompts, the shared box their voicemails
land in, and the live telephony presence the phone widget reads are all
declared on top of the Twilio-shaped user record.
"""

from datetime import timedelta

from odoo import api, fields, models

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
    voicemail_email_enabled = fields.Boolean(
        string='Voicemail to Email', default=True,
        help='Send voicemail recordings and transcriptions via email.')
    callerid_number = fields.Many2one(
        'connect.twilio.number', ondelete='restrict',
        help='DID used as this user\'s caller ID when no outgoing caller ID is set.')
    presence_status = fields.Selection([
        ('offline', 'Offline'),
        ('available', 'Available'),
        ('on_call', 'On Call'),
        ('on_hold', 'On Hold'),
    ], string='Presence', default='offline',
        help='Current telephony presence status')
    presence_updated = fields.Datetime(string='Presence Updated',
        help='Last presence status change timestamp')

    _PRESENCE_HEARTBEAT_SECONDS = 60

    @api.model
    def update_presence(self, status):
        """Write the current user's telephony presence from the softphone.

        Concurrent overlapping RPCs of this method serialise-fail on the
        same connect_user row. The client queues last-write-wins; this
        method also skips a write when the stored status is unchanged and
        the heartbeat is still fresh, so retries do not occupy HTTP workers.
        """
        user = self.sudo().search([('user', '=', self.env.user.id)], limit=1)
        if not user:
            return True
        vals = {}
        status_field = user._fields.get('presence_status')
        if status_field and status_field.store and user.presence_status != status:
            vals['presence_status'] = status
        now = fields.Datetime.now()
        last = user.presence_updated
        if not last or (now - last) >= timedelta(seconds=self._PRESENCE_HEARTBEAT_SECONDS) or vals:
            vals['presence_updated'] = now
        if not vals:
            return True
        user.with_context(skip_sync=True, no_clear_cache=True).write(vals)
        if 'presence_status' in vals:
            self.env['bus.bus']._sendone(
                'connect_presence',
                'presence_update',
                {
                    'user_id': user.id,
                    'status': status,
                    'name': user.name,
                },
            )
        return True

    @api.model
    def get_all_presence(self):
        """Presence of every connect.user for the directory widget."""
        users = self.sudo().search([])
        result = []
        for u in users:
            exten = getattr(u, 'twilio_exten_number', None) or getattr(u, 'exten_number', None) or ''
            result.append({
                'id': u.id,
                'name': u.name,
                'exten_number': exten,
                'presence_status': u.presence_status,
                'user_id': u.user.id if u.user else False,
            })
        return result
