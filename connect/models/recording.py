# -*- coding: utf-8 -*-

import base64
import json
import logging
import os
import requests
from tempfile import NamedTemporaryFile
from odoo import fields, models, api, release, SUPERUSER_ID
from odoo.exceptions import ValidationError
from .settings import format_connect_response, debug, HTTP_DOWNLOAD_TIMEOUT

logger = logging.getLogger(__name__)


class Recording(models.Model):
    _name = 'connect.recording'
    _description = 'Recording'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'id'
    _order = 'id desc'

    call = fields.Many2one('connect.call', ondelete='set null')
    channel = fields.Many2one('connect.channel', ondelete='set null')
    partner = fields.Many2one('res.partner', ondelete='set null')
    sid = fields.Char('SID', readonly=True, required=True)
    # It's a channel sid actually.
    call_sid = fields.Char(required=True, string='Channel SID', readonly=True)
    caller_user = fields.Many2one(related='call.caller_user', store=True, readonly=False)
    called_user = fields.Many2one('res.users', ondelete='set null')
    caller_number = fields.Char()
    called_number = fields.Char()
    media_url = fields.Char()
    price = fields.Char()
    price_unit = fields.Char()
    source = fields.Char()
    duration = fields.Integer()
    duration_human = fields.Char(compute='_get_duration_human')
    start_time = fields.Datetime()
    status = fields.Char()
    attachment_id = fields.Many2one('ir.attachment', string='Recording File', ondelete='set null', readonly=True, copy=False)
    if release.version_info[0] >= 17.0:
        recording_widget = fields.Html(compute='_get_recording_widget', string='Recording', sanitize=False)
    else:
        recording_widget = fields.Char(compute='_get_recording_widget', string='Recording')
    ############## TRANSCRIPTION FIELDS ######################################
    transcript = fields.Text()
    transcription_token = fields.Char()
    transcription_error = fields.Char()
    transcription_price = fields.Char()
    summary = fields.Html()
    list_view_summary = fields.Html(compute='_get_list_view_summary')

    ############## TRANSCRIPTION METHODS #####################################

    def _get_summary_context(self):
        """Build template context for summary prompt rendering.

        Available placeholders: {caller_name}, {called_name}, {caller_number},
        {called_number}, {direction}, {number_name}, {number_description}
        """
        if not self:
            return {
                'caller_number': '',
                'called_number': '',
                'direction': 'unknown',
                'caller_name': 'Unknown Caller',
                'called_name': 'Unknown',
                'number_name': '',
                'number_description': '',
            }
        self.ensure_one()
        ctx = {
            'caller_number': self.caller_number or '',
            'called_number': self.called_number or '',
            'direction': (self.call.direction or 'unknown') if self.call else 'unknown',
        }
        # Resolve caller display name: prefer partner, then user, then number
        if self.call and self.call.partner:
            ctx['caller_name'] = self.call.partner.display_name
        elif self.caller_user:
            ctx['caller_name'] = self.caller_user.display_name
        else:
            ctx['caller_name'] = self.caller_number or 'Unknown Caller'
        # Resolve called display name: prefer user, then number
        if self.called_user:
            ctx['called_name'] = self.called_user.display_name
        else:
            ctx['called_name'] = self.called_number or 'Unknown'
        # Look up connect.number for identity context
        number = False
        if self.call:
            our_number = self.called_number if ctx['direction'] == 'inbound' else self.caller_number
            if our_number:
                number = self.env['connect.number'].sudo().search(
                    [('phone_number', '=', our_number)], limit=1)
        ctx['number_name'] = (number.friendly_name or number.phone_number) if number else ''
        ctx['number_description'] = (number.description or '') if number else ''
        return ctx

    def _render_summary_prompt(self, summary_prompt, transcript=''):
        """Render summary prompt template with call context.

        Uses safe formatting: unrecognized {placeholders} are left as-is.
        """
        ctx = self._get_summary_context()
        ctx['transcript'] = transcript

        class SafeDict(dict):
            def __missing__(self, key):
                return '{' + key + '}'

        try:
            return summary_prompt.format_map(SafeDict(ctx))
        except Exception:
            logger.warning('Failed to render summary prompt template, using as-is')
            return summary_prompt

    def _get_transcription_model(self):
        """Return transcription model name. Override to make configurable."""
        return 'whisper-1'

    def _get_completion_model(self):
        """Return completion/summary model name. Override to make configurable."""
        return os.environ.get('OPENAI_COMPLETION_MODEL', 'gpt-4o')

    def _download_recording_audio(self):
        """Download recording audio to a temporary file. Returns path or None."""
        if self.attachment_id:
            data = base64.b64decode(self.attachment_id.sudo().datas)
            with NamedTemporaryFile(delete=False, suffix='.mp3') as f:
                f.write(data)
                return f.name
        if not self.media_url:
            return None
        account_sid, auth_token = self.env['connect.settings'].sudo()._get_client_credentials()
        response = requests.get(self.media_url, stream=True, auth=(account_sid, auth_token),
                                timeout=HTTP_DOWNLOAD_TIMEOUT)
        response.raise_for_status()
        with NamedTemporaryFile(delete=False, suffix=".mp3") as temp_file:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    temp_file.write(chunk)
            return temp_file.name

    def transcribe_recording(self, openai_api_key, summary_prompt):
        result = {}
        temp_file_path = None
        try:
            client = self.env['connect.settings'].get_openai_client()
            temp_file_path = self._download_recording_audio()
            if not temp_file_path:
                result['transcription_error'] = 'Recording media not available'
                return
            file_size = os.path.getsize(temp_file_path)
            if file_size > 26214400:
                error_msg = 'File exceeds size limit (26MB). Please use the Elevenlabs module for larger files.'
                logger.error(error_msg)
                result['transcription_error'] = error_msg
                return
            transcript_text = self._call_transcription_api(client, temp_file_path)
            result['transcript'] = transcript_text
            result.update(self.make_summary(client, summary_prompt, transcript_text))
            result['transcription_error'] = False
        except Exception as e:
            logger.exception('Transcribe error for recording id=%s sid=%s: %s', self.id, self.sid, e)
            result['transcription_error'] = str(e)
        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                os.remove(temp_file_path)
            self.write(result)

    def _call_transcription_api(self, client, audio_path):
        """Call transcription API with fallback for non-standard models.

        First tries verbose_json with timestamps (Whisper native).
        Falls back to plain text if the model/proxy doesn't support it.
        """
        model = self._get_transcription_model()
        # Try verbose format with timestamps first
        try:
            with open(audio_path, 'rb') as audio_file:
                transcript = client.audio.transcriptions.create(
                    model=model, file=audio_file,
                    response_format='verbose_json',
                    timestamp_granularities=["segment"])
            segments = ''
            for s in transcript.segments:
                seconds = int(s.start)
                ts = f"{int(seconds // 3600):02d}:{int((seconds % 3600) // 60):02d}:{int(seconds % 60):02d}"
                segments += '{} {}\n'.format(ts, s.text)
            return segments
        except Exception as e:
            logger.info(
                'verbose_json transcription failed (%s), falling back to text format: %s',
                model, e)
        # Fallback: plain text transcription (works with more providers/proxies)
        with open(audio_path, 'rb') as audio_file:
            transcript = client.audio.transcriptions.create(
                model=model, file=audio_file,
                response_format='text')
        return transcript if isinstance(transcript, str) else str(transcript)

    def make_summary(self, client, summary_prompt, transcript):
        logger.info('Make summary!')
        try:
            # Render prompt template with call context
            transcript_in_prompt = '{transcript}' in summary_prompt
            rendered = self._render_summary_prompt(summary_prompt, transcript)
            if transcript_in_prompt:
                # Transcript embedded in prompt via {transcript} placeholder
                messages = [{'role': 'user', 'content': rendered}]
            else:
                # Legacy: prompt and transcript as separate messages
                messages = [
                    {'role': 'user', 'content': rendered},
                    {'role': 'user', 'content': transcript},
                ]
            response = client.chat.completions.create(
                model=self._get_completion_model(),
                messages=messages,
                temperature=float(os.environ.get('OPENAI_COMPLETION_TEMPERATURE', 0.5)),
                max_tokens=int(os.environ.get('OPENAI_COMPLETION_MAX_TOKENS', 4096)),
                top_p=float(os.environ.get('OPENAI_COMPLETION_TOP_P', 1.0)),
                frequency_penalty=float(os.environ.get('OPENAI_COMPLETION_FREQUENCY_PENALTY', 0.0)),
                presence_penalty=float(os.environ.get('OPENAI_COMPLETION_PRESENCE_PENALTY', 0.0)),
            )
            logger.info('%s', response.usage)
            return {'summary': response.choices[0].message.content.strip('\n\n')}
        except Exception as e:
            logger.exception('Summary error for recording id=%s: %s', self.id, e)
            return {'transcription_error': str(e)}

    def get_transcript(self, fail_silently=False):
        self.ensure_one()
        openai_key = self.env['connect.settings'].sudo().get_param('openai_api_key')
        if not openai_key:
            if fail_silently:
                logger.warning('OpenAI key is not set! Transcription will not be available.')
                return False
            else:
                raise ValidationError('OpenAI key is not set!')
        summary_prompt = self.env['connect.settings'].get_param('summary_prompt')
        if not self.media_url:
            raise ValidationError('Recording is not available yet!')
        self.transcribe_recording(openai_key, summary_prompt)

    def update_transcript(self, data):
        # Update transcription and also erase access token.
        self.ensure_one()
        transcription_price = data.get('transcription_price')
        if transcription_price:
            # Round
            transcription_price = round(transcription_price, 2)
        vals = {
            'transcript': data.get('transcript'),
            'transcription_price': str(transcription_price),
            'summary': data.get('summary'),
            # Reset the token
            'transcription_token': False,
            'transcription_error': data.get('transcription_error')
        }
        self.with_context(tracking_disable=True).write(vals)
        # Update call summary.
        if self.call:
            self.call.summary = data.get('summary')
            # Reload calls view when transcription has come.
            self.env['connect.settings'].connect_reload_view('connect.call')
        # Reload views when transcription has come.
        self.env['connect.settings'].connect_reload_view('connect.recording')
        # Notify user
        if data.get('notify_uid'):
            self.env['connect.settings'].connect_notify(
                'Transcript updated', notify_uid=data['notify_uid'])

##########  END OF TRANSCRIPTION METHODS #########################################################

    def _get_recording_widget(self):
        proxy_recordings = self.env['connect.settings'].sudo().get_param('proxy_recordings')
        for rec in self:
            if rec.attachment_id:
                src = '/web/content/{}?download=false'.format(rec.attachment_id.id)
            elif rec.media_url:
                src = '/connect/recording/{}'.format(rec.id) if proxy_recordings else rec.media_url
            else:
                rec.recording_widget = ''
                continue
            rec.recording_widget = (
                '<audio id="sound_file" preload="auto" controls="controls">'
                '<source src="{}"/></audio>'.format(src)
            )

    def _store_as_attachment(self):
        """Download recording from Twilio and store as ir.attachment."""
        self.ensure_one()
        if not self.media_url:
            return
        settings = self.env['connect.settings'].sudo()
        account_sid, auth_token = settings._get_client_credentials()
        response = requests.get(
            self.media_url, auth=(account_sid, auth_token),
            timeout=HTTP_DOWNLOAD_TIMEOUT
        )
        response.raise_for_status()
        attachment = self.env['ir.attachment'].sudo().create({
            'name': 'recording_{}.mp3'.format(self.sid),
            'datas': base64.b64encode(response.content).decode(),
            'res_model': self._name,
            'res_id': self.id,
            'mimetype': 'audio/mpeg',
        })
        self.write({'attachment_id': attachment.id})
        if settings.get_param('delete_twilio_recording'):
            self._delete_from_twilio()
        return attachment

    def _delete_from_twilio(self):
        """Queue deletion of this recording from the provider.

        Runs post-commit so a failed/retried transaction never destroys a
        remote recording the database has no committed copy of.
        """
        self.ensure_one()
        self.env['connect.settings'].defer_twilio_recording_delete(self.sid)

    def _get_list_view_summary(self):
        for rec in self:
            rec.list_view_summary = rec.summary

    @api.model
    def prepare_data(self, rec):
        data = {}
        for field in ['sid', 'call_sid', 'media_url', 'price', 'price_unit',
                      'duration', 'source', 'start_time','status']:
            data[field] = getattr(rec, field)
            if field in ['start_time', 'date_created', 'date_updated']:
                # Parse 2024-05-29 21:44:48+00:00
                data[field] = data[field].utcnow()
        channel = self.env['connect.channel'].search([('sid', '=', rec.call_sid)])
        data['call'] = channel.call.id
        data['channel'] = channel.id
        return data

    def sync(self):
        client = self.env['connect.settings'].get_client()
        for rec in self:
            if not rec.media_url:
                continue
            recording = client.recordings(rec.sid).fetch()
            data = self.prepare_data(recording)
            rec.write(data)

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get('skip_transcription'):
            return super().create(vals_list)
        transcript_calls = self.env['connect.settings'].sudo().get_param('transcript_calls')
        recs = super(Recording, self.with_context(
            mail_create_nosubscribe=True, mail_create_nolog=True)).create(vals_list)
        # Commit to the database so that transcription error will not break the recording.
        self.env.cr.commit()
        if transcript_calls:
            for rec in recs:
                try:
                    rec.get_transcript(fail_silently=True)
                except Exception as e:
                    logger.exception('Transcript error: %s', e)
        return recs

    def _notify_recording_started(self, params, channel):
        """Push a bus event so the phone UI immediately reflects auto-recording state.

        Dial-level recordings live on the parent call SID, not the browser client SID.
        For incoming calls: recording is on the parent, browser client is a child channel.
        For outgoing calls: recording is on the browser's own channel (as caller).
        """
        call_sid = params['CallSid']
        recording_sid = params['RecordingSid']

        uids = set()
        # Outgoing calls: recording lives on the caller's own channel
        if channel and channel.caller_user:
            uids.add(channel.caller_user.id)
        # Incoming calls: browser client is a child of the parent call SID
        child_channels = self.env['connect.channel'].search([('parent_sid', '=', call_sid)])
        for ch in child_channels:
            if ch.called_user:
                uids.add(ch.called_user.id)

        payload = {'recording_call_sid': call_sid, 'recording_sid': recording_sid}
        for uid in uids:
            self.env['bus.bus']._sendone(
                'connect_actions_{}'.format(uid), 'recording_started', payload)

    @api.model
    def on_recording_status(self, params):
        self = self.sudo()
        debug(self, 'On recording status: %s' % json.dumps(params, indent=2))
        # Todo: RecordingChannels
        data = {
            'sid': params['RecordingSid'],
            'call_sid': params['CallSid'],
            'duration': params['RecordingDuration'],
            'status': params['RecordingStatus']
        }
        call = None
        channel = self.env['connect.channel'].search([('sid', '=', params['CallSid'])], limit=1)
        if channel:
            call = channel.call
        # Conference recordings include ConferenceSid; fall back to call lookup
        if not call and params.get('ConferenceSid'):
            call = self.env['connect.call'].search([
                ('conference_sid', '=', params['ConferenceSid'])
            ], limit=1)
            if not call:
                # Try matching by conference friendly name
                friendly_name = params.get('FriendlyName', '')
                if friendly_name:
                    call = self.env['connect.call'].search([
                        ('conference_name', '=', friendly_name)
                    ], limit=1)
        called_user = False
        if channel:
            called_user = channel.search([
                '|', ('sid', '=', params['CallSid']),
                ('parent_channel', '=', channel.id),
                ('called_user', '!=', False)], limit=1).called_user
            data['channel'] = channel.id
        if call:
            data['call'] = call.id
            data['partner'] = call.partner.id
            if called_user:
                data['called_user'] = called_user.id
            data['caller_number'] = call.caller
            data['called_number'] = call.called

        # For in-progress callbacks (auto-recording just started), notify the UI and
        # skip record creation — the completed callback will create the full record.
        if params['RecordingStatus'] == 'in-progress':
            self._notify_recording_started(params, channel)
            return True

        # Fetch recording
        client = self.env['connect.settings'].get_client()
        try:
            recording = client.recordings(data['sid']).fetch()
            data.update(self.prepare_data(recording))
        except Exception as e:
            logger.exception(format_connect_response(e))
        # Idempotency: skip if this RecordingSid was already processed (Twilio may retry)
        existing = self.search([('sid', '=', data['sid'])], limit=1)
        if existing:
            logger.info('Duplicate recording webhook ignored for RecordingSid=%s', data['sid'])
            existing.write({'status': data.get('status', existing.status)})
            return True
        recording = self.create(data)
        recording_storage = self.env['connect.settings'].sudo().get_param('recording_storage', 'twilio')
        if recording_storage and recording_storage != 'twilio':
            try:
                recording._store_as_attachment()
            except Exception as e:
                logger.exception('Failed to store recording %s: %s', data.get('sid'), e)
        return True

    @api.depends('duration')
    def _get_duration_human(self):
        for record in self:
            if record.duration is not None:
                # Compute minutes and seconds
                minutes = record.duration // 60
                seconds = record.duration % 60
                # Format human-readable time as MM:SS
                record.duration_human = '{:02}:{:02}'.format(minutes, seconds)
            else:
                record.duration_human = "00:00"

    @api.constrains('summary')
    def _sync_summary(self):
        # When recording transcription summary is set we update related object summary.
        if self.call:
            self.with_user(SUPERUSER_ID).call.summary = self.summary

    def unlink(self):
        attachments = self.mapped('attachment_id').filtered(lambda a: a.id)
        result = super().unlink()
        if attachments:
            attachments.sudo().unlink()
        return result
