# -*- coding: utf-8 -*-
"""Audio-layer wiring for calls.

A call gets its spoken prompts from the TTS abstraction, and a voicemail
left on it is filed into a box and moved through a stage. Both are audio
layer concerns declared on top of the Twilio-shaped call record.
"""

import base64
import os
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

import requests

from odoo import Command, api, fields, models
from odoo.exceptions import ValidationError


class Call(models.Model):
    _name = 'connect.call'
    _inherit = ['connect.call', 'connect.tts.mixin']

    voicemail_stage_id = fields.Many2one(
        'connect.voicemail_stage', string='Stage', index=True, tracking=True,
        group_expand='_group_expand_voicemail_stage',
        help='Handling stage of the voicemail left on this call.',
    )
    voicemail_box_id = fields.Many2one(
        'connect.voicemail_box', ondelete='set null', string='Voicemail Box',
        index=True, tracking=True, readonly=True,
        help='Shared box this call belongs to. Set via the user or callflow that received the voicemail.')
    voicemail_assignee_ids = fields.Many2many(
        'res.users', 'connect_call_voicemail_assignee_rel', 'call_id', 'user_id',
        string='Assignees', domain="[('share', '=', False)]",
        help='Internal users responsible for handling this voicemail.',
    )
    voicemail_transcript = fields.Text(
        string='Voicemail Transcript',
        help='Transcript generated for this voicemail.',
    )
    voicemail_attachment_id = fields.Many2one(
        'ir.attachment', string='Voicemail File', ondelete='set null',
        readonly=True, copy=False,
        help='Stored voicemail media when external recording storage is enabled.',
    )
    voicemail_sid = fields.Char(
        string='Voicemail SID', readonly=True, copy=False,
        help='Provider recording identifier for this voicemail.',
    )
    callflow_id = fields.Many2one(
        'connect.twilio.callflow', ondelete='set null', string='Callflow',
        index=True, readonly=True,
        help='Provider callflow that routed this call to voicemail.',
    )
    call_result = fields.Selection(
        [
            ('answered', 'Answered'),
            ('missed', 'Missed'),
            ('voicemail', 'Voicemail'),
            ('failed', 'Failed'),
            ('busy', 'Busy'),
        ],
        string='Result', compute='_compute_call_result', store=True,
        help='Call outcome the voicemail queue filters on.',
    )

    def _group_expand_voicemail_stage(self, stages, domain):
        return stages.search([])

    @api.model
    def _file_voicemail(self, call_sid, box, callflow=None):
        """File the call on ``call_sid`` into ``box`` as it goes to voicemail.

        Records the callflow that took the caller to voicemail when the call
        has none, and stamps the box only when the call has no box yet. The
        recording status webhook that follows carries only the CallSid, so
        this runs while the voicemail TwiML is built.
        """
        if not call_sid:
            return
        channel = self.env['connect.channel'].sudo().search(
            [('sid', '=', call_sid)], limit=1)
        call = channel.call.sudo()
        if not call:
            return
        if callflow and not call.callflow_id:
            call.callflow_id = callflow.id
        if box and not call.voicemail_box_id:
            call.voicemail_box_id = box.id

    def process_call_event(self, channel, error_data=None):
        call_id = super().process_call_event(channel, error_data)
        # A user's voicemails land in that user's box.
        if (channel.call and not channel.call.voicemail_box_id
                and channel.called_pbx_user.voicemail_box_id):
            channel.call.voicemail_box_id = channel.called_pbx_user.voicemail_box_id
        return call_id

    @api.depends('voicemail_url', 'status', 'direction', 'answered_user')
    def _compute_call_result(self):
        for record in self:
            if record.voicemail_url:
                record.call_result = 'voicemail'
            elif record.status == 'busy':
                record.call_result = 'busy'
            elif record.status in ('failed', 'canceled'):
                record.call_result = 'failed'
            elif record.direction == 'incoming' and not record.answered_user:
                record.call_result = 'missed'
            else:
                record.call_result = 'answered'

    def action_assign_to_me(self):
        self.ensure_one()
        if self.env.user not in self.voicemail_assignee_ids:
            self.voicemail_assignee_ids = [Command.link(self.env.uid)]

    def action_transcribe(self):
        self.ensure_one()
        if self.recording:
            self.recording.get_transcript()
        elif self.voicemail_url:
            self._transcribe_voicemail()

    def _download_voicemail_audio(self):
        """Write the voicemail audio to a temporary file and return its path.

        Provider credentials go only to an HTTPS URL of the provider that
        hosts the recording, with no userinfo and no redirects.
        """
        self.ensure_one()
        if self.voicemail_attachment_id:
            data = base64.b64decode(self.voicemail_attachment_id.sudo().datas)
            with NamedTemporaryFile(delete=False, suffix='.mp3') as stream:
                stream.write(data)
                return stream.name
        if not self.voicemail_url:
            return None
        media_url = urlsplit(self.voicemail_url)
        if media_url.scheme != 'https' or media_url.username or media_url.password:
            raise ValidationError(
                'Voicemail media must use an HTTPS provider URL without user credentials.')
        auth = self.env['connect.settings'].sudo()._get_media_auth(self.voicemail_url)
        if not auth:
            raise ValidationError(
                'Voicemail media must be hosted by the configured provider.')
        response = requests.get(
            self.voicemail_url, stream=True, auth=auth,
            allow_redirects=False, timeout=30,
        )
        try:
            if 300 <= response.status_code < 400:
                raise ValidationError('Voicemail media redirects are not allowed.')
            response.raise_for_status()
            with NamedTemporaryFile(delete=False, suffix='.mp3') as stream:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        stream.write(chunk)
                return stream.name
        finally:
            response.close()

    def _transcribe_voicemail(self):
        client = self.env['connect.settings'].get_openai_client()
        if not client:
            return
        path = None
        try:
            path = self._download_voicemail_audio()
            if not path:
                return
            with open(path, 'rb') as audio_file:
                result = client.audio.transcriptions.create(
                    model='whisper-1', file=audio_file, response_format='text')
            self.voicemail_transcript = result if isinstance(result, str) else str(result)
        finally:
            if path and os.path.exists(path):
                os.remove(path)

    def action_view_partner(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'res.partner',
            'res_id': self.partner.id,
            'view_mode': 'form',
            'target': 'current',
        }
