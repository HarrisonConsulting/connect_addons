# -*- coding: utf-8 -*-

import base64
import json
import logging
import os
import re
import requests
import time
from tempfile import NamedTemporaryFile
from urllib.parse import urljoin
from markupsafe import Markup
import uuid
from datetime import timedelta
from psycopg2 import OperationalError
from odoo import fields, models, api, SUPERUSER_ID, tools, Command
from odoo.exceptions import ValidationError
from twilio.twiml.voice_response import VoiceResponse, Say, Dial, Conference, Client, Number, Sip
from .settings import debug, HTTP_DOWNLOAD_TIMEOUT

logger = logging.getLogger(__name__)

CALL_END_STATUSES = ['completed', 'busy', 'failed', 'no-answer', 'canceled']

IGNORE_ERROR_CODES = ['32009']


class Call(models.Model):
    _name = 'connect.call'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'connect.tts.mixin']
    _description = 'Call'
    _order = 'id desc'

    name = fields.Char(compute='_get_name')
    channels = fields.One2many('connect.channel', 'call', readonly=True)
    recording = fields.Many2one('connect.recording', compute='_get_recording_data')
    transcript = fields.Text(compute='_get_recording_data', string='Recording Transcript')
    transcription_error = fields.Char(compute='_get_recording_data', string='Transcription Error')
    recording_widget = fields.Html(compute='_get_recording_data', sanitize=False)
    recording_icon = fields.Html(compute='_get_recording_data', string='R')
    summary = fields.Html()
    called = fields.Char(readonly=True, index=True)
    caller = fields.Char(readonly=True, index=True)
    parent_call = fields.Many2one('connect.call', ondelete='cascade', readonly=True)
    partner = fields.Many2one('res.partner', ondelete='set null')
    partner_img = fields.Binary(related='partner.image_1920', string='Partner Image')
    direction = fields.Char(index=True, readonly=True)
    call_type = fields.Selection([
        ('phone', 'Phone'),
        ('whatsapp', 'WhatsApp')
    ], default='phone', index=True)
    status = fields.Char(readonly=True, index=True)
    duration = fields.Integer(string='Seconds', readonly=True, index=True)
    duration_minutes = fields.Float(string='Minutes', compute='_get_duration_human', store=True)
    duration_human = fields.Char(compute='_get_duration_human', string='Duration', store=True)
    # PBX users are Connect SIP or Client users.
    caller_pbx_user = fields.Many2one('connect.user', ondelete='set null', string='Caller PBX User', readonly=True)
    answered_pbx_user = fields.Many2one('connect.user', ondelete='set null', string='Answered PBX User', readonly=True)
    called_pbx_users = fields.Many2many('connect.user', readonly=True)
    # Users are Odoo accounts.
    caller_user = fields.Many2one('res.users', string='Caller User', ondelete='set null', readonly=True)
    caller_user_img = fields.Binary(related='caller_user.image_1920')
    called_users = fields.Many2many('res.users', readonly=True)
    answered_user = fields.Many2one('res.users', ondelete='set null', string='Answered User', readonly=True)
    answered_user_img = fields.Binary(related='answered_user.image_1920', string='Answered User Avatar')
    # Transfer tracking fields
    transferred_users = fields.Many2many('res.users', 'connect_call_transfer_rel', 'call_id', 'user_id', string='Transferred Users', readonly=True)
    completed_by_user = fields.Many2one('res.users', ondelete='set null', string='Completed By', readonly=True)
    transfer_context = fields.Json(string='Transfer Context', readonly=True, help='Temporary storage for transfer targets during webhook processing')
    call_pattern = fields.Selection([
        ('ring_group', 'Ring Group (Multiple Users)'),
        ('direct_call', 'Direct Call (Single User)')
    ], string='Call Pattern', readonly=True, help='Detected call pattern: ring group vs direct call')
    # Scheduled fields.
    scheduled_datetime = fields.Datetime()
    # Voicemail fields
    voicemail_url = fields.Char(readonly=True)
    voicemail_duration = fields.Integer(readonly=True)
    voicemail_icon = fields.Html(compute='_get_voicemail_icon', string='V', store=True)
    voicemail_widget = fields.Html(compute='_get_voicemail_widget', string='VoiceMail', sanitize=False)
    voicemail_attachment_id = fields.Many2one('ir.attachment', string='Voicemail File', ondelete='set null', readonly=True, copy=False)
    voicemail_sid = fields.Char(string='Voicemail SID', readonly=True, copy=False)
    # Voicemail management fields
    voicemail_stage_id = fields.Many2one(
        'connect.voicemail_stage', string='Stage', index=True, tracking=True,
        group_expand='_group_expand_voicemail_stage', help="",
    )
    voicemail_assignee_ids = fields.Many2many(
        'res.users', 'connect_call_voicemail_assignee_rel', 'call_id', 'user_id',
        string='Assignees', domain="[('share', '=', False)]",
        help="Internal users responsible for handling this voicemail"
    )
    voicemail_transcript = fields.Text(string='Voicemail Transcript', help="")
    voicemail_box_id = fields.Many2one(
        'connect.voicemail_box', ondelete='set null', string='Voicemail Box',
        index=True, tracking=True, readonly=True,
        help='Shared box this call belongs to. Set via the user or callflow that received the voicemail.')
    callflow_id = fields.Many2one(
        'connect.callflow', ondelete='set null', string='Callflow',
        index=True, readonly=True,
        help='Callflow that routed this call to voicemail.')
    # Reference, to submit call history and summary.
    ref = fields.Reference(selection=[('res.partner', 'Partner')], compute='_get_ref')
    has_error = fields.Boolean(index=True)
    error_code = fields.Char(readonly=True)
    error_message = fields.Text(readonly=True)
    # Call price fields
    price = fields.Float(string='Call Price', readonly=True, digits=(10, 3))
    price_unit = fields.Char(string='Price Unit', readonly=True, help='The currency unit for call price (e.g., USD)')
    price_currency = fields.Char(string='Price Currency', readonly=True, default='USD')
    call_sid = fields.Char(string='Twilio Call SID', readonly=True, index=True, help='Twilio CallSid for fetching price information')
    is_price_fetched = fields.Boolean(string='Price Fetched', default=False, readonly=True, index=True, help='Indicates if call price has been fetched from Twilio API')
    # Conference call control fields
    conference_sid = fields.Char(string='Conference SID', readonly=True, help='Twilio Conference SID when call is promoted to conference')
    conference_name = fields.Char(string='Conference Name', readonly=True, help='Twilio Conference friendly name')
    is_on_hold = fields.Boolean(string='On Hold', default=False, help='Whether the remote party is currently on hold')
    is_finalized = fields.Boolean(string='Finalized', default=False, help='Whether call finalization has completed')
    # Analytics fields
    hour_of_day = fields.Integer(
        string='Hour of Day', compute='_compute_analytics_fields', store=True,
        help='Hour when call started (0-23)')
    day_of_week = fields.Char(
        string='Day of Week', compute='_compute_analytics_fields', store=True,
        help='Day of week when call started')
    is_missed = fields.Boolean(
        string='Missed Call', compute='_compute_analytics_fields', store=True,
        help='Call was missed (incoming, unanswered)')
    call_result = fields.Selection([
        ('answered', 'Answered'),
        ('missed', 'Missed'),
        ('voicemail', 'Voicemail'),
        ('failed', 'Failed'),
        ('busy', 'Busy'),
    ], string='Result', compute='_compute_analytics_fields', store=True,
        help='Computed call outcome for analytics')

    @api.depends('create_date', 'direction', 'status', 'answered_user', 'voicemail_url')
    def _compute_analytics_fields(self):
        for rec in self:
            # Time dimensions
            if rec.create_date:
                rec.hour_of_day = rec.create_date.hour
                rec.day_of_week = rec.create_date.strftime('%A')
            else:
                rec.hour_of_day = 0
                rec.day_of_week = ''
            # restricted reads via sudo — the recompute may run as a low-priv user
            r = rec.sudo()
            voicemail_url, status, direction = r.voicemail_url, r.status, r.direction
            # Call result classification
            if voicemail_url:
                rec.call_result = 'voicemail'
                rec.is_missed = False
            elif status == 'busy':
                rec.call_result = 'busy'
                rec.is_missed = False
            elif status in ('failed', 'canceled'):
                rec.call_result = 'failed'
                rec.is_missed = False
            elif direction == 'incoming' and not rec.answered_user and status in ('no-answer', 'completed'):
                rec.call_result = 'missed'
                rec.is_missed = True
            elif rec.answered_user or status == 'completed':
                rec.call_result = 'answered'
                rec.is_missed = False
            else:
                rec.call_result = 'missed' if direction == 'incoming' else 'failed'
                rec.is_missed = direction == 'incoming'

    def _get_name(self):
        for rec in self:
            try:
                is_missed_call = (
                    (rec.direction == 'incoming' and
                     rec.status in ['no-answer', 'busy', 'failed'] and
                     not rec.answered_user)
                    or
                    (rec.transferred_users and not rec.completed_by_user)
                )
                if is_missed_call:
                    caller_name = None
                    caller_number = rec.caller
                    if rec.partner:
                        caller_name = rec.partner.name
                    elif rec.caller_user:
                        caller_name = rec.caller_user.name
                    if caller_name and caller_number:
                        caller_display = f"{caller_name} ({caller_number})"
                    elif caller_name:
                        caller_display = caller_name
                    elif caller_number:
                        caller_display = caller_number
                    else:
                        caller_display = "Unknown"
                    rec.name = f"Missed call from {caller_display}"
                else:
                    started = fields.Datetime.context_timestamp(rec, rec.create_date)
                    formatted_time = fields.Datetime.to_string(started)
                    rec.name = '{} {} call at {}'.format(rec.status, rec.direction, formatted_time).capitalize()
            except Exception:
                logger.exception('Call name compute error:')
                rec.name = str(rec.id)

    def _get_ref(self):
        for rec in self:
            if rec.partner:
                rec.ref = 'res.partner,{}'.format(rec.partner.id)
            else:
                rec.ref = False

    def _get_recording_data(self):
        # Make one query to get all records.
        recordings = self.env['connect.recording'].search([('call', 'in', [k.id for k in self])])
        for rec in self:
            recording = recordings.filtered(lambda x: x.call.id == rec.id)
            if recording:
                # Make sure we take the last recording (fix for Elevenlabs agent recording)
                recording = max(recording, key=lambda x: x.id)
                rec.recording = recording
                rec.transcript = recording.transcript
                rec.transcription_error = recording.transcription_error or ''
                rec.recording_icon = '<span class="fa fa-file-sound-o"/>'
                rec.recording_widget = recording.recording_widget
            else:
                rec.recording_icon = ''
                rec.transcript = ''
                rec.transcription_error = ''
                rec.recording = False
                rec.recording_widget = ''

    def _get_voicemail_widget(self):
        proxy_recordings = self.env['connect.settings'].sudo().get_param('proxy_recordings')
        for rec in self:
            if rec.voicemail_attachment_id:
                src = '/web/content/{}?download=false'.format(rec.voicemail_attachment_id.id)
            elif rec.voicemail_url:
                src = '/connect/voicemail/{}'.format(rec.id) if proxy_recordings else rec.voicemail_url
            else:
                rec.voicemail_widget = ''
                continue
            rec.voicemail_widget = (
                '<audio id="sound_file" preload="auto" controls="controls">'
                '<source src="{}"/></audio>'.format(src)
            )

    @api.depends('voicemail_url')
    def _get_voicemail_icon(self):
        for rec in self:
            if rec.voicemail_url:
                rec.voicemail_icon = '<span class="fa fa-envelope-o"/>'
            else:
                rec.voicemail_icon = ''

    def action_transcribe(self):
        self.ensure_one()
        if self.recording:
            self.recording.get_transcript()
        elif self.voicemail_url:
            self._transcribe_voicemail()

    def _transcribe_voicemail(self):
        client = self.env['connect.settings'].get_openai_client()
        if not client:
            return
        temp_file_path = None
        try:
            if self.voicemail_attachment_id:
                data = base64.b64decode(self.voicemail_attachment_id.sudo().datas)
                with NamedTemporaryFile(delete=False, suffix='.mp3') as f:
                    f.write(data)
                    temp_file_path = f.name
            else:
                account_sid, auth_token = self.env['connect.settings'].sudo()._get_client_credentials()
                response = requests.get(
                    self.voicemail_url, stream=True,
                    auth=(account_sid, auth_token),
                    timeout=HTTP_DOWNLOAD_TIMEOUT)
                response.raise_for_status()
                with NamedTemporaryFile(delete=False, suffix='.mp3') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                    temp_file_path = f.name
            if not temp_file_path:
                return
            with open(temp_file_path, 'rb') as audio_file:
                result = client.audio.transcriptions.create(
                    model='whisper-1', file=audio_file, response_format='text')
            self.voicemail_transcript = result if isinstance(result, str) else str(result)
        except Exception as e:
            logger.exception('Voicemail transcription error call id=%s: %s', self.id, e)
        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                os.remove(temp_file_path)

    def action_view_partner(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'res.partner',
            'res_id': self.partner.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_summarize(self):
        """Generate summary from transcript. Transcribes first if needed."""
        self.ensure_one()
        if self.recording:
            # Transcribe if no transcript exists yet
            if not self.recording.transcript:
                self.recording.get_transcript()
            # If transcript is available now, (re)generate summary
            if self.recording.transcript:
                summary_prompt = self.env['connect.settings'].get_param('summary_prompt')
                client = self.env['connect.settings'].get_openai_client()
                if client and summary_prompt:
                    result = self.recording.make_summary(
                        client, summary_prompt, self.recording.transcript)
                    if result.get('summary'):
                        self.recording.write({'summary': result['summary']})

    @api.depends('duration')
    def _get_duration_human(self):
        for record in self:
            if record.duration is not None:
                minutes = record.duration // 60
                seconds = record.duration % 60
                record.duration_human = '{:02}:{:02}'.format(minutes, seconds)
                record.duration_minutes = record.duration / 60.0
            else:
                record.duration_minutes = 0
                record.duration_human = "00:00"

    def _detect_call_pattern(self):
        """
        Detect the call pattern from explicit channel tagging.
        Returns:
            'ring_group': Multiple users rang simultaneously
            'direct_call': Single user called initially
        """
        self.ensure_one()
        if self.call_pattern:
            return self.call_pattern
        if not self.channels:
            return None
        child_channels = self.channels.filtered(lambda c: c.parent_channel and c.called_pbx_user)
        if not child_channels:
            return None
        ring_group_channels = child_channels.filtered(lambda c: c.call_source == 'ring_group')
        direct_call_channels = child_channels.filtered(lambda c: c.call_source == 'direct_call')
        if ring_group_channels:
            pattern = 'ring_group'
        elif direct_call_channels:
            pattern = 'direct_call'
        else:
            return self._detect_call_pattern_fallback()
        return pattern

    def _detect_call_pattern_fallback(self):
        """Fallback pattern detection using timing-based logic."""
        child_channels = self.channels.filtered(lambda c: c.parent_channel and c.called_pbx_user)
        initial_called_users = set()
        for channel in child_channels:
            if channel.called_pbx_user and channel.called_pbx_user.user:
                initial_called_users.add(channel.called_pbx_user.user.id)
        pattern = 'ring_group' if len(initial_called_users) > 1 else 'direct_call'
        return pattern

    def _finalize_call_details(self):
        """
        Called once when all channels are closed to do final call processing.
        Acquires an exclusive row lock to prevent concurrent finalization from
        racing webhooks. Idempotent: skips if already finalized.
        """
        self.ensure_one()
        # Idempotent guard — if already finalized, nothing to do
        if self.is_finalized:
            logger.info(f"Call {self.id}: Already finalized, skipping")
            return
        # Acquire exclusive lock. Use NOWAIT so we fail fast if another worker
        # holds the lock, then retry once after a short delay.
        lock_acquired = False
        for attempt in range(2):
            try:
                self.env.cr.execute(
                    "SELECT id FROM connect_call WHERE id = %s FOR UPDATE NOWAIT",
                    (self.id,)
                )
                if self.env.cr.fetchone():
                    lock_acquired = True
                    break
            except Exception:
                # Lock contention — rollback the failed statement and retry
                self.env.cr.rollback()
                if attempt == 0:
                    logger.info(f"Call {self.id}: Lock contention on finalization, retrying in 0.5s")
                    time.sleep(0.5)
                    # Re-check idempotent guard after retry delay — the other
                    # worker may have completed finalization
                    self.invalidate_recordset(['is_finalized'])
                    if self.is_finalized:
                        logger.info(f"Call {self.id}: Finalized by another worker during retry")
                        return
        if not lock_acquired:
            logger.warning(f"Call {self.id}: Could not acquire finalization lock after retries, will be retried by cron")
            return
        # Refresh in-memory values after acquiring the lock so we see the
        # latest database state (another transaction may have committed).
        self.invalidate_recordset()
        # Double-check after refresh in case another worker finalized between
        # our initial check and lock acquisition
        if self.is_finalized:
            logger.info(f"Call {self.id}: Already finalized (detected after lock), skipping")
            return
        logger.info(f"=== FINALIZING CALL DETAILS FOR CALL {self.id} ===")
        if not self.call_pattern:
            detected_pattern = self._detect_call_pattern()
            if detected_pattern:
                self.call_pattern = detected_pattern
                logger.info(f"Call {self.id}: Set call pattern to '{detected_pattern}'")
        if self.call_pattern == 'direct_call':
            self._populate_user_fields_direct_call()
        elif self.call_pattern == 'ring_group':
            self._populate_user_fields_ring_group()
        else:
            logger.warning(f"Call {self.id}: Unknown call pattern '{self.call_pattern}', using fallback logic")
            self._populate_user_fields_fallback()
        self._set_final_call_status()
        logger.info(f"Call {self.id}: Final status='{self.status}', answered_user='{self.answered_user.login if self.answered_user else None}', completed_by_user='{self.completed_by_user.login if self.completed_by_user else None}', transferred_users={len(self.transferred_users)}")
        # Mark as finalized and clean up transfer context
        self.is_finalized = True
        self.transfer_context = False

    @api.model
    def _retry_stuck_finalizations(self):
        """Cron: retry finalization for calls stuck as unfinalized for >5 minutes."""
        cutoff = fields.Datetime.subtract(fields.Datetime.now(), minutes=5)
        stuck_calls = self.search([
            ('is_finalized', '=', False),
            ('create_date', '<', cutoff),
            ('status', 'in', CALL_END_STATUSES),
        ], limit=50)
        if stuck_calls:
            logger.info('Cron: Found %d stuck unfinalized calls to retry', len(stuck_calls))
        for call in stuck_calls:
            try:
                call._finalize_call_details()
            except Exception:
                logger.exception('Cron: Failed to finalize call %s', call.id)

    def _set_final_call_status(self):
        """Simplified call status logic based on answered_user field."""
        self.ensure_one()
        if self.direction == 'outgoing':
            self.status = 'completed'
            logger.info(f"Call {self.id}: Status set to 'completed' (outgoing call)")
        elif self.answered_user:
            self.status = 'completed'
            logger.info(f"Call {self.id}: Status set to 'completed' (answered by {self.answered_user.login})")
        else:
            # Last resort: check root channel for evidence of answered call
            root_channel = self.channels.filtered(lambda c: not c.parent_channel)
            if root_channel and root_channel[0].status == 'completed' and root_channel[0].duration and root_channel[0].duration > 0:
                self.status = 'completed'
                logger.info(f"Call {self.id}: Status set to 'completed' (root channel completed with {root_channel[0].duration}s, child webhook likely lost)")
            else:
                channel_statuses = self.channels.mapped('status')
                if 'failed' in channel_statuses:
                    self.status = 'failed'
                    logger.info(f"Call {self.id}: Status set to 'failed' (channel failed)")
                elif 'no-answer' in channel_statuses:
                    self.status = 'no-answer'
                    logger.info(f"Call {self.id}: Status set to 'no-answer' (at least one channel rang)")
                elif 'busy' in channel_statuses:
                    self.status = 'busy'
                    logger.info(f"Call {self.id}: Status set to 'busy' (all channels busy)")
                else:
                    self.status = 'no-answer'
                    logger.info(f"Call {self.id}: Status set to 'no-answer' (default)")

    def _populate_user_fields_direct_call(self):
        """Populate user fields for direct call pattern."""
        self.ensure_one()
        logger.info(f"Call {self.id}: Populating user fields for direct call pattern")
        if self.direction == 'outgoing':
            self._populate_outgoing_call_user_fields()
            return
        user_channels = self.channels.filtered(lambda c: c.called_pbx_user and c.called_pbx_user.user)
        if not user_channels:
            logger.warning(f"Call {self.id}: No channels with users found for direct call")
            return
        user_channels_by_time = user_channels.sorted('create_date')
        completed_channels = user_channels.filtered(lambda c: c.status == 'completed')
        if self.transferred_users and completed_channels:
            initial_answered_channels = completed_channels.filtered(
                lambda c: c.called_pbx_user.user not in self.transferred_users
            )
            if initial_answered_channels:
                if len(initial_answered_channels) > 1:
                    answered_channel = initial_answered_channels.sorted('id')[0]
                else:
                    answered_channel = initial_answered_channels[0]
                self.answered_user = answered_channel.called_pbx_user.user
                self.answered_pbx_user = answered_channel.called_pbx_user
                logger.info(f"Call {self.id}: answered_user set to {self.answered_user.login} (initial answerer, excluding transfers)")
            else:
                logger.info(f"Call {self.id}: No initial answerer found (all completed channels are transfers)")
        elif completed_channels:
            if len(completed_channels) > 1:
                answered_channel = completed_channels.sorted('id')[0]
            else:
                answered_channel = completed_channels[0]
            self.answered_user = answered_channel.called_pbx_user.user
            self.answered_pbx_user = answered_channel.called_pbx_user
            logger.info(f"Call {self.id}: answered_user set to {self.answered_user.login} (completed channel, no transfers)")
        else:
            # Safety net: if root channel completed with duration, call was answered
            # but child channel webhook was lost (e.g. SerializationFailure rollback)
            root_channel = self.channels.filtered(lambda c: not c.parent_channel)
            if root_channel and root_channel[0].status == 'completed' and root_channel[0].duration and root_channel[0].duration > 0:
                if len(user_channels) == 1:
                    self.answered_user = user_channels[0].called_pbx_user.user
                    self.answered_pbx_user = user_channels[0].called_pbx_user
                    logger.info(f"Call {self.id}: answered_user inferred from root channel duration ({root_channel[0].duration}s, sole called user {self.answered_user.login})")
                else:
                    logger.warning(f"Call {self.id}: Root channel completed with {root_channel[0].duration}s but {len(user_channels)} users called - cannot determine answerer")
            else:
                logger.info(f"Call {self.id}: No completed channels found - leaving answered_user empty")
        if self.transferred_users:
            if not self.completed_by_user:
                all_user_channels = self.channels.filtered(lambda c: c.called_pbx_user and c.called_pbx_user.user)
                completed_channels = all_user_channels.filtered(lambda c: c.status == 'completed')
                transfer_completed_channels = completed_channels.filtered(
                    lambda c: c.called_pbx_user.user in self.transferred_users
                )
                if transfer_completed_channels:
                    if len(transfer_completed_channels) > 1:
                        final_channel = transfer_completed_channels.sorted('id')[-1]
                    else:
                        final_channel = transfer_completed_channels[0]
                    self.completed_by_user = final_channel.called_pbx_user.user
                    logger.info(f"Call {self.id}: completed_by_user set to transfer recipient {self.completed_by_user.login} (from channel)")
                else:
                    logger.info(f"Call {self.id}: Transfer failed - completed_by_user left empty for missed call notifications")
            else:
                logger.info(f"Call {self.id}: completed_by_user already set by extension handler: {self.completed_by_user.login}")
        else:
            self.completed_by_user = self.answered_user
            if self.completed_by_user:
                logger.info(f"Call {self.id}: completed_by_user set to original answerer {self.completed_by_user.login} (no transfer)")

    def _populate_outgoing_call_user_fields(self):
        """Populate user fields for outgoing calls."""
        self.ensure_one()
        logger.info(f"Call {self.id}: Populating user fields for outgoing call")
        outbound_channel = None
        for channel in self.channels:
            if channel.technical_direction == 'outbound-dial':
                outbound_channel = channel
                break
        if outbound_channel:
            external_number = outbound_channel.called_number
            if outbound_channel.partner and outbound_channel.partner.user_id:
                self.called_users = [(4, outbound_channel.partner.user_id.id)]
                logger.info(f"Call {self.id}: called_users set to Odoo contact {outbound_channel.partner.name}")
            else:
                self.called_users = [(5,)]
                logger.info(f"Call {self.id}: External party {external_number} has no Odoo user - called_users cleared")
            external_answered = (outbound_channel.status in ['in-progress', 'completed'] and
                               outbound_channel.duration and outbound_channel.duration > 0)
            if external_answered:
                if outbound_channel.partner and outbound_channel.partner.user_id:
                    self.answered_user = outbound_channel.partner.user_id
                    logger.info(f"Call {self.id}: answered_user set to Odoo contact {outbound_channel.partner.name}")
                else:
                    logger.info(f"Call {self.id}: External party {external_number} answered, but no Odoo contact found")
            else:
                logger.info(f"Call {self.id}: External party didn't answer (status: {outbound_channel.status})")
        internal_channels = self.channels.filtered(lambda c: c.called_pbx_user and c.called_pbx_user.user)
        if internal_channels:
            completed_internal = internal_channels.filtered(lambda c: c.status == 'completed')
            if completed_internal:
                if len(completed_internal) > 1:
                    completed_channel = completed_internal.sorted('id')[-1]
                else:
                    completed_channel = completed_internal[0]
                self.completed_by_user = completed_channel.called_pbx_user.user
                logger.info(f"Call {self.id}: completed_by_user set to transfer recipient {self.completed_by_user.login}")
            else:
                logger.info(f"Call {self.id}: Transfer attempted but no transfer recipient completed - completed_by_user remains empty")
        else:
            self._set_original_caller_as_completer()

    def _set_original_caller_as_completer(self):
        """Helper to set original caller as completed_by_user for outgoing calls"""
        caller_channel = None
        for channel in self.channels:
            if channel.caller_pbx_user and channel.caller_pbx_user.user:
                caller_channel = channel
                break
        if caller_channel:
            self.completed_by_user = caller_channel.caller_pbx_user.user
            logger.info(f"Call {self.id}: completed_by_user set to original caller {self.completed_by_user.login}")
        else:
            logger.warning(f"Call {self.id}: Could not identify original caller for outgoing call")

    def _populate_user_fields_ring_group(self):
        """Populate user fields for ring group pattern."""
        self.ensure_one()
        logger.info(f"Call {self.id}: Populating user fields for ring group pattern")
        ring_group_channels = self.channels.filtered(lambda c: c.call_source == 'ring_group' and c.called_pbx_user and c.called_pbx_user.user)
        if not ring_group_channels:
            logger.warning(f"Call {self.id}: No ring_group channels with users found")
            return
        completed_ring_channels = ring_group_channels.filtered(lambda c: c.status == 'completed')
        if not completed_ring_channels:
            # Safety net: check root channel for evidence of answered call
            root_channel = self.channels.filtered(lambda c: not c.parent_channel)
            if root_channel and root_channel[0].status == 'completed' and root_channel[0].duration and root_channel[0].duration > 0:
                unique_users = list({ch.called_pbx_user.user for ch in ring_group_channels if ch.called_pbx_user.user})
                if len(unique_users) == 1:
                    self.answered_user = unique_users[0]
                    self.answered_pbx_user = ring_group_channels[0].called_pbx_user
                    logger.info(f"Call {self.id}: answered_user inferred from root channel duration ({root_channel[0].duration}s, sole ring group user {self.answered_user.login})")
                else:
                    logger.warning(f"Call {self.id}: Root channel completed with {root_channel[0].duration}s but {len(unique_users)} ring group users - cannot determine answerer")
            else:
                logger.info(f"Call {self.id}: No completed ring_group channels found - no one answered")
            return
        if self.transferred_users:
            genuine_ring_answered = completed_ring_channels.filtered(
                lambda c: c.called_pbx_user.user not in self.transferred_users
            )
            if genuine_ring_answered:
                completed_ring_channels = genuine_ring_answered
        if len(completed_ring_channels) > 1:
            answered_channel = completed_ring_channels.sorted('id')[0]
        else:
            answered_channel = completed_ring_channels[0]
        self.answered_user = answered_channel.called_pbx_user.user
        self.answered_pbx_user = answered_channel.called_pbx_user
        logger.info(f"Call {self.id}: answered_user set to {self.answered_user.login} (answered from ring group)")
        if self.transferred_users:
            if not self.completed_by_user:
                transfer_completed_channels = []
                for user in self.transferred_users:
                    user_channels = self.channels.filtered(lambda c: c.called_user and c.called_user.id == user.id and c.status == 'completed')
                    transfer_completed_channels.extend(user_channels)
                if transfer_completed_channels:
                    latest_channel = sorted(transfer_completed_channels, key=lambda c: c.id)[-1]
                    self.completed_by_user = latest_channel.called_user
                    logger.info(f"Call {self.id}: completed_by_user set to transfer recipient {self.completed_by_user.login} (from channel)")
                else:
                    logger.info(f"Call {self.id}: Transfer failed - completed_by_user left empty for missed call notifications")
            else:
                logger.info(f"Call {self.id}: completed_by_user already set by extension handler: {self.completed_by_user.login}")
        else:
            self.completed_by_user = self.answered_user
            logger.info(f"Call {self.id}: completed_by_user set to answerer {self.answered_user.login} (no transfer)")

    def _populate_user_fields_fallback(self):
        """Fallback user field population when pattern detection fails."""
        self.ensure_one()
        logger.info(f"Call {self.id}: Using fallback user field population")
        completed_channels = self.channels.filtered(lambda c: c.status == 'completed' and c.called_pbx_user and c.called_pbx_user.user)
        if completed_channels:
            sorted_channels = completed_channels.sorted('write_date')
            first_channel = sorted_channels[0]
            self.answered_user = first_channel.called_pbx_user.user
            self.answered_pbx_user = first_channel.called_pbx_user
            last_channel = sorted_channels[-1]
            self.completed_by_user = last_channel.called_pbx_user.user
            logger.info(f"Call {self.id}: Fallback - answered_user={self.answered_user.login}, completed_by_user={self.completed_by_user.login}")

    # =========================================================================
    # STATUS RESOLUTION METHODS
    # =========================================================================
    # Priority-based status resolution for determining call outcome.
    # When multiple users are dialed, we resolve to a single business-meaningful status.
    # Priority (highest wins): answered > voicemail > rejected > busy > missed > failed
    # =========================================================================

    def _resolve_call_status(self, child_channels):
        """
        Resolve call status from multiple child channel outcomes.

        Priority (highest wins):
        1. answered - ANY child completed (someone picked up)
        2. voicemail - handled separately by voicemail webhook
        3. rejected - ANY child was rejected
        4. busy - ANY child was busy
        5. missed - all children no-answer/canceled
        6. failed - ALL children failed

        Returns: (status, completed_child or None)
        """
        child_statuses = [ch.status for ch in child_channels]

        # Priority 1: ANSWERED - if any child completed
        completed_child = next(
            (ch for ch in child_channels if ch.status == 'completed'),
            None
        )
        if completed_child:
            return 'answered', completed_child

        # Priority 2: VOICEMAIL - handled by voicemail webhook, not here
        # (voicemail_url is set asynchronously after this runs)

        # Priority 3: REJECTED - if any child was rejected
        if 'rejected' in child_statuses:
            return 'rejected', None

        # Priority 4: BUSY - if any child was busy
        if 'busy' in child_statuses:
            return 'busy', None

        # Priority 5: FAILED - if ALL children failed
        if all(s == 'failed' for s in child_statuses):
            return 'failed', None

        # Priority 6: MISSED - default for no-answer, canceled, or mix
        return 'missed', None

    def _map_twilio_status(self, twilio_status):
        """
        Map Twilio's channel status to our business-meaningful status.

        Twilio statuses: queued, ringing, in-progress, completed, busy,
                         no-answer, canceled, failed
        Our statuses: answered, voicemail, rejected, busy, missed, failed
        """
        mapping = {
            'completed': 'answered',
            'busy': 'busy',
            'no-answer': 'missed',
            'canceled': 'missed',
            'failed': 'failed',
            # In-progress states (shouldn't hit this for final status)
            'queued': 'missed',
            'ringing': 'missed',
            'in-progress': 'answered',  # If we get this as final, call was connected
        }
        return mapping.get(twilio_status, 'missed')

    def add_transferred_user(self, user):
        """Add a user to the transferred_users field when a transfer is initiated."""
        self.ensure_one()
        if user and hasattr(user, 'id'):
            current_transfer_ids = self.transferred_users.ids
            if user.id not in current_transfer_ids:
                self.transferred_users = [(4, user.id)]
                if hasattr(self.__class__, '_webhook_expectations'):
                    call_key = f"call_{self.id}"
                    expectations = self.__class__._webhook_expectations.get(call_key, {})
                    if 'ring_group' in expectations:
                        ring_expectation = expectations['ring_group']
                        current_expected = ring_expectation.get('expected_count', 0)
                        if current_expected > 1:
                            new_expected = current_expected - 1
                            ring_expectation['expected_count'] = new_expected
                            logger.info(f"Call {self.id}: Reduced ring_group expectation from {current_expected} to {new_expected} due to transfer")
                            all_call_sids = list(ring_expectation.get('call_sid_states', {}).keys())
                            terminal_sids = [sid for sid, state in ring_expectation.get('call_sid_states', {}).items() if state.get('terminal', False)]
                            if len(all_call_sids) >= new_expected and len(terminal_sids) >= new_expected:
                                logger.info(f"Call {self.id}: ring_group expectation now fulfilled with reduced count - clearing expectation")
                                del expectations['ring_group']
                                if not expectations:
                                    del self.__class__._webhook_expectations[call_key]
                self._set_webhook_expectation('transfer', {
                    'expected_count': 1,
                    'received_count': 0,
                    'target_user_id': user.id,
                    'target_user_login': user.login
                })
                logger.info(f"Call {self.id}: Transfer initiated to {user.login} (added to transferred_users)")
                if not self.call_pattern:
                    detected_pattern = self._detect_call_pattern()
                    if detected_pattern:
                        self.call_pattern = detected_pattern

    def store_transfer_context(self, dial_call_sid, target_user):
        """Store temporary transfer context for webhook processing."""
        self.ensure_one()
        if not dial_call_sid or not target_user:
            return
        current_context = self.transfer_context or {}
        current_context[dial_call_sid] = {
            'user_id': target_user.id,
            'user_login': target_user.login
        }
        self.transfer_context = current_context
        logger.info(f"Call {self.id}: Stored transfer context for {dial_call_sid} -> {target_user.login}")

    def get_transfer_target(self, dial_call_sid):
        """Get transfer target from temporary context storage."""
        self.ensure_one()
        if not self.transfer_context or not dial_call_sid:
            return None
        context_data = self.transfer_context.get(dial_call_sid)
        if context_data and 'user_id' in context_data:
            user = self.env['res.users'].sudo().browse(context_data['user_id'])
            if user.exists():
                logger.info(f"Call {self.id}: Retrieved transfer target from context: {user.login}")
                return user
        return None

    def store_external_call_leg(self, external_call_sid):
        """Store external call leg SID for outgoing call transfers."""
        self.ensure_one()
        if not external_call_sid:
            return
        current_context = self.transfer_context or {}
        current_context['_external_leg'] = external_call_sid
        self.transfer_context = current_context
        logger.info(f"Call {self.id}: Stored external call leg SID: {external_call_sid}")

    def get_external_call_leg(self):
        """Get external call leg SID for outgoing call transfers."""
        self.ensure_one()
        if not self.transfer_context:
            return None
        external_leg = self.transfer_context.get('_external_leg')
        if external_leg:
            logger.info(f"Call {self.id}: Retrieved external call leg SID: {external_leg}")
            return external_leg
        return None

    def clear_transfer_context(self):
        """Clear temporary transfer context after call processing is complete."""
        self.ensure_one()
        if self.transfer_context:
            logger.info(f"Call {self.id}: Clearing transfer context")
            self.transfer_context = None

    @classmethod
    def _cleanup_old_webhook_expectations(cls):
        """Clean up old expectations (older than 10 minutes)"""
        import datetime
        if not hasattr(cls, '_webhook_expectations'):
            return
        cutoff = fields.Datetime.now() - datetime.timedelta(minutes=10)
        to_remove = []
        for call_key, expectations in cls._webhook_expectations.items():
            empty_expectations = []
            for source, data in expectations.items():
                if data['timestamp'] < cutoff:
                    empty_expectations.append(source)
            for source in empty_expectations:
                del expectations[source]
            if not expectations:
                to_remove.append(call_key)
        for call_key in to_remove:
            del cls._webhook_expectations[call_key]
        if to_remove:
            logger.info(f"Cleaned up webhook expectations for {len(to_remove)} completed calls")

    def _set_webhook_expectation(self, source, data):
        """Set expectation for incoming webhook data with per-CallSid tracking"""
        import datetime
        import random
        if random.randint(1, 50) == 1:
            self.__class__._cleanup_old_webhook_expectations()
        if not hasattr(self.__class__, '_webhook_expectations'):
            self.__class__._webhook_expectations = {}
        call_key = f"call_{self.id}"
        if call_key not in self.__class__._webhook_expectations:
            self.__class__._webhook_expectations[call_key] = {}
        expected_call_sids = data.get('expected_call_sids', [])
        self.__class__._webhook_expectations[call_key][source] = {
            'timestamp': fields.Datetime.now(),
            'expected_count': data.get('expected_count', 1),
            'received_count': 0,
            'expected_call_sids': expected_call_sids,
            'call_sid_states': {},
            **{k: v for k, v in data.items() if k not in ['expected_count', 'received_count', 'expected_call_sids']}
        }
        if expected_call_sids:
            logger.info(f"Call {self.id}: Set {source} webhook expectation - expecting CallSids: {expected_call_sids}")
        else:
            logger.info(f"Call {self.id}: Set {source} webhook expectation - expecting {data.get('expected_count', 1)} channels")

    def _update_webhook_expectation_callsid(self, source, call_sid, call_status):
        """Update CallSid state and check if expectation is complete based on terminal states"""
        if not hasattr(self.__class__, '_webhook_expectations'):
            return
        call_key = f"call_{self.id}"
        if call_key not in self.__class__._webhook_expectations:
            return
        expectations = self.__class__._webhook_expectations[call_key]
        if source not in expectations:
            return
        expectation = expectations[source]
        is_terminal = call_status in CALL_END_STATUSES
        expectation['call_sid_states'][call_sid] = {
            'status': call_status,
            'terminal': is_terminal
        }
        logger.info(f"Call {self.id}: {source} CallSid tracking - {call_sid} status: {call_status} (terminal: {is_terminal})")
        expected_count = expectation.get('expected_count', 1)
        all_call_sids = list(expectation['call_sid_states'].keys())
        terminal_sids = [sid for sid, state in expectation['call_sid_states'].items() if state['terminal']]
        logger.info(f"Call {self.id}: {source} CallSids seen: {len(all_call_sids)}, terminal: {len(terminal_sids)}, expected: {expected_count}")
        if len(all_call_sids) >= expected_count and len(terminal_sids) >= expected_count:
            logger.info(f"Call {self.id}: {source} expectation fulfilled - all expected CallSids reached terminal states")
            del expectations[source]
            if not expectations:
                logger.info(f"Call {self.id}: All webhook expectations complete - removing call from tracking")
                del self.__class__._webhook_expectations[call_key]
        else:
            if len(all_call_sids) < expected_count:
                reason = f"waiting for {expected_count - len(all_call_sids)} more CallSids"
            else:
                non_terminal = expected_count - len(terminal_sids)
                reason = f"waiting for {non_terminal} CallSids to reach terminal state"
            logger.info(f"Call {self.id}: {source} expectation not yet fulfilled - {reason}")

    def _has_pending_webhooks(self):
        """Check if we're still expecting webhook data"""
        if not hasattr(self.__class__, '_webhook_expectations'):
            return False
        call_key = f"call_{self.id}"
        if call_key not in self.__class__._webhook_expectations:
            return False
        expectations = self.__class__._webhook_expectations[call_key]
        if not expectations:
            return False
        import datetime
        cutoff = fields.Datetime.now() - datetime.timedelta(seconds=60)
        active_expectations = False
        timed_out_sources = []
        for source, data in expectations.items():
            timestamp = data['timestamp']
            if timestamp > cutoff:
                active_expectations = True
            else:
                timed_out_sources.append(source)
        for source in timed_out_sources:
            logger.warning(f"Call {self.id}: {source} expectation timed out after 60 seconds")
            del expectations[source]
        if not expectations:
            del self.__class__._webhook_expectations[call_key]
        if timed_out_sources and not active_expectations:
            logger.warning(f"Call {self.id}: All webhook expectations timed out, proceeding with finalization")
        return active_expectations

    def _clear_webhook_expectations(self, source=None):
        """Clear webhook expectations for this call, optionally for a specific source"""
        if not hasattr(self.__class__, '_webhook_expectations'):
            return
        call_key = f"call_{self.id}"
        if call_key not in self.__class__._webhook_expectations:
            return
        if source:
            expectations = self.__class__._webhook_expectations[call_key]
            if source in expectations:
                logger.info(f"Call {self.id}: Clearing {source} webhook expectation due to pattern change")
                del expectations[source]
                if not expectations:
                    del self.__class__._webhook_expectations[call_key]
        else:
            logger.info(f"Call {self.id}: Clearing all webhook expectations")
            del self.__class__._webhook_expectations[call_key]

    def write(self, vals):
        return super().write(vals)

    @api.model
    def on_call_status(self, params):
        self = self.sudo()
        # Tracks whether this webhook produced a change worth pushing to every
        # open connect.call view. Every channel-leg status event used to
        # broadcast unconditionally, which turned a single call that hunts a
        # non-answering agent into a company-wide ~1 Hz view reload.
        call_created = False
        # Create channel
        channel = self.env['connect.channel'].on_call_status(params)
        if not channel:
            logger.error('No channel returned from on_call_status!')
            return False
        if not channel.parent_channel and not channel.call:
            # A queue agent-dial leg belongs to the CALLER's call, never to a
            # new one of its own. Its parent is resolved from parent_sid (the
            # task's caller_sid), and when that fails to resolve — the task
            # carried no caller_sid, or the caller channel is already gone —
            # falling through to create() minted one bogus customer-facing
            # call PER DIAL ATTEMPT. During a redial storm that is what filled
            # the call list with a run of duplicate calls against a single
            # partner, and each one broadcast a view reload. Reservation and
            # attempt bookkeeping for this leg already ran in
            # connect.channel.on_call_status above, so there is nothing left
            # to do. hasattr guard: is_queue_agent_dial only exists when
            # connect_enqueue is installed (same idiom as call_source below).
            if getattr(channel, 'is_queue_agent_dial', False):
                logger.info(
                    'Orphan queue agent-dial leg %s (parent_sid=%s did not '
                    'resolve); not creating a call for it.',
                    channel.sid, channel.parent_sid)
                return False
            # Create a new call.
            if channel.technical_direction == 'outbound-api':
                # Click2call originated call.
                debug(self, 'outbound-api channel direction.')
                direction = 'outgoing'
            elif channel.technical_direction == 'inbound' and channel.caller_pbx_user:
                # Outgoing call from SIP or Client.
                debug(self, 'inbound channel direction with caller_pbx_user.')
                direction = 'outgoing'
            elif channel.technical_direction == 'inbound' and not channel.caller_pbx_user:
                # Incoming DID call
                debug(self, 'inbound channel direction without caller_pbx_user. Assuming DID call.')
                direction = 'incoming'
            else:
                # Default
                debug(self, 'Setting default call direction to outgoing.')
                direction = 'outgoing'
            # Set call pattern for outgoing and internal calls (always direct_call since they're one-to-one)
            call_pattern = 'direct_call' if direction in ('outgoing', 'internal') else False
            call_vals = {
                'partner': channel.partner.id,
                'called': channel.called_number,
                'caller': channel.caller_number,
                'status': channel.status,
                'caller_pbx_user': channel.caller_pbx_user.id,
                'caller_user': channel.caller_user.id,
                'direction': direction,
                'call_type': channel.call_type or 'phone',
                'call_pattern': call_pattern,
            }
            # Set parent_call if in queue context (agent dial linked to customer call)
            parent_call_id = self.env.context.get('queue_parent_call_id')
            if parent_call_id:
                call_vals['parent_call'] = parent_call_id
            call = self.with_context(tracking_disable=True).create(call_vals)
            channel.call = call
            # A new row in the call list/kanban is worth a client reload;
            # the per-leg churn below is not. See the reload gate at the
            # end of this method.
            call_created = True
        elif channel.parent_channel and channel.parent_channel.call:
            # Secondary channel, assign the call from the parent.
            channel.call = channel.parent_channel.call
            # Detect internal calls: both sides are PBX users (extension-to-extension).
            # This also reclassifies outgoing->internal when the child channel reveals the called user.
            if channel.caller_pbx_user and channel.parent_channel.called_pbx_user:
                channel.call.direction = 'internal'
            elif channel.called_pbx_user and channel.parent_channel.caller_pbx_user:
                if not channel.call.transferred_users:
                    channel.call.direction = 'internal'
        if (channel.call.direction == 'incoming' and params.get('CallStatus') == 'initiated' and
                params.get('To').startswith('sip:')):
            # Desktop notification only for SIP calls.
            channel.connect_notify()
        # DATABASE LOCKING: Acquire exclusive lock on call record to prevent concurrent modifications.
        # All writes to the call record MUST happen after this lock to prevent SerializationFailure
        # when multiple child call webhooks arrive simultaneously (e.g. Client + SIP legs).
        self.env.cr.execute("SELECT id FROM connect_call WHERE id = %s FOR UPDATE", (channel.call.id,))
        # Set called pbx users
        if channel.called_pbx_user:
            if channel.called_pbx_user.id not in channel.call.called_pbx_users.ids:
                channel.call.called_pbx_users = [(4, channel.called_pbx_user.id)]
        # Check if we need to set a partner from child channel
        if not channel.call.partner and channel.partner:
            channel.call.partner = channel.partner
        # Set called from 2nd call leg for click2call external calls.
        if channel.parent_channel and channel.parent_channel.technical_direction == 'outbound-api':
            channel.call.called = channel.called_number
        # Update call duration based on longest channel (actual elapsed time)
        if channel.call:
            if channel.call.channels:
                channel_durations = [d for d in channel.call.channels.mapped('duration') if d]
                channel.call.duration = max(channel_durations) if channel_durations else 0
            # Pattern detection from explicit tagging
            if not channel.call.call_pattern:
                detected_pattern = channel.call._detect_call_pattern()
                if detected_pattern:
                    channel.call.call_pattern = detected_pattern
                    logger.info(f"Call {channel.call.id}: Pattern detection set to '{detected_pattern}'")
        # Set called users - all called users including transfer recipients
        if channel.called_user:
            if channel.called_user.id not in channel.call.called_users.ids:
                channel.call.called_users = [(4, channel.called_user.id)]
                logger.info(f"Added {channel.called_user.login} to called_users (call_source: {getattr(channel, 'call_source', 'None')}) for call {channel.call.id}")
        # Stamp voicemail box from called_pbx_user when configured
        if not channel.call.voicemail_box_id and channel.called_pbx_user.voicemail_box_id:
            channel.call.voicemail_box_id = channel.called_pbx_user.voicemail_box_id.id
        # Update webhook expectations for child call webhooks
        if params.get('ParentCallSid'):
            call_status = params.get('CallStatus')
            call_sid = params.get('CallSid')
            if hasattr(channel, 'call_source') and channel.call_source:
                expectation_source = channel.call_source
            else:
                expectation_source = 'ring_group'
            channel.call._update_webhook_expectation_callsid(expectation_source, call_sid, call_status)
        # Determine finalization authority
        call_finalized = False
        is_parent_call_webhook = not params.get('ParentCallSid')
        if channel.call.direction == 'outgoing':
            if params.get('ParentCallSid'):
                parent_completed = any(ch.sid == params.get('ParentCallSid') and ch.status in CALL_END_STATUSES
                                     for ch in channel.call.channels)
                can_trigger_finalization = parent_completed
            else:
                can_trigger_finalization = True
        else:
            can_trigger_finalization = is_parent_call_webhook
        # Register call only when ALL channels have ended AND no pending webhook expectations AND can trigger finalization
        all_channels_ended = all(ch.status in CALL_END_STATUSES for ch in channel.call.channels)
        has_pending_webhooks = channel.call._has_pending_webhooks()
        if (all_channels_ended and
            params.get('CallStatus') in CALL_END_STATUSES and
            not has_pending_webhooks and
            can_trigger_finalization):
            current_called_users = set(channel.call.called_users.ids)
            current_status = channel.call.status
            logger.info(f"Call {channel.call.id}: All conditions met for finalization")
            channel.call._finalize_call_details()
            # Finalization is where status/duration/answered_user settle —
            # the transition users actually need to see.
            call_finalized = True
            new_called_users = set(channel.call.called_users.ids)
            status_changed = channel.call.status != current_status
            users_changed = current_called_users != new_called_users
            if users_changed or current_status != 'busy':
                self.register_call(channel, params)
            # Fetch call price if enabled in settings
            if self.env['connect.settings'].sudo().get_param('fetch_call_prices'):
                self.save_call_price(channel.call, params)
        else:
            if not all_channels_ended:
                reason = "channels still active"
            elif has_pending_webhooks:
                reason = "pending webhook expectations"
            elif not can_trigger_finalization:
                reason = "child call webhook (parent call authority)"
            else:
                reason = "channel not ending"
            logger.info(f"Call {channel.call.id}: Finalization deferred - {reason}")
            # Still fetch call price even when finalization is deferred
            if params.get('CallStatus') in CALL_END_STATUSES:
                if self.env['connect.settings'].sudo().get_param('fetch_call_prices'):
                    self.save_call_price(channel.call, params)
        call_errored = False
        if params.get('ErrorCode') and params.get('ErrorCode') not in IGNORE_ERROR_CODES:
            call_errored = not channel.call.has_error
            channel.call.update({
                'has_error': True,
                'error_code': params.get('ErrorCode'),
                'error_message': params.get('ErrorMessage')
            })
            # Notify caller user on errors on outgoing calls.
            user = channel.caller_user or channel.call.caller_user
            if channel.call.direction == 'outgoing' and user:
                if 'No International Permission' in params.get('ErrorMessage', ''):
                    message_text = re.sub(
                        r'(https?://\S+)',
                        r'<strong><a target="_blank" href="\1">your Twilio Console</a></strong>',
                        params.get('ErrorMessage', ''))
                else:
                    message_text = params.get('ErrorMessage', '')
                self.env['connect.settings'].connect_notify(
                    notify_uid=user.id,
                    title="Call Error",
                    message=message_text,
                    warning=True,
                )
        # Reload call views only on the transitions a list/kanban actually
        # renders differently: the call appearing, the call settling, or the
        # call going into error. Intermediate child-leg webhooks move only
        # `duration`, which is not worth a full view reload — and broadcasting
        # on them is what let one looping call refresh every user's screen
        # roughly once a second for four hours.
        if call_created or call_finalized or call_errored:
            self.env['connect.settings'].connect_reload_view('connect.call')
        return channel.call.id

    @api.model
    def on_vm_recording_status(self, params):
        debug(self.sudo(), 'On recording status: %s' % json.dumps(params, indent=2))
        channel = self.sudo().env['connect.channel'].search([('sid', '=', params['CallSid'])])
        if channel and channel.call:
            updates = {
                'voicemail_url': params.get('RecordingUrl'),
                'voicemail_duration': int(params.get('RecordingDuration')),
                'voicemail_sid': params.get('RecordingSid'),
            }
            if channel.call.status != 'answered':
                updates['status'] = 'voicemail'
            # Set initial stage and assignees if not already set
            if not channel.call.voicemail_stage_id:
                stage = self.env['connect.voicemail_stage'].sudo().search([], order='sequence asc', limit=1)
                if stage:
                    updates['voicemail_stage_id'] = stage.id
            if not channel.call.voicemail_assignee_ids and channel.call.called_users:
                updates['voicemail_assignee_ids'] = [Command.set(channel.call.called_users.ids)]
            channel.call.write(updates)
            # Notify assignees via bus for realtime kanban update
            try:
                channel.call._notify_voicemail_new()
            except Exception as e:
                logger.exception('Voicemail bus notification error: %s', e)
            # Transfer voicemail to configured storage
            recording_storage = self.env['connect.settings'].sudo().get_param('recording_storage', 'twilio')
            if recording_storage and recording_storage != 'twilio':
                try:
                    channel.call._store_voicemail_as_attachment()
                except Exception as e:
                    logger.exception('Voicemail storage error: %s', e)
            # Send voicemail notification email
            try:
                channel.call._send_voicemail_email()
            except Exception as e:
                logger.exception('Voicemail email error: %s', e)
        return True

    def _notify_voicemail_new(self):
        self.ensure_one()
        caller_display = self.partner.name if self.partner else (self.caller or 'Unknown')
        payload = {
            'call_id': self.id,
            'caller': caller_display,
            'duration': self.voicemail_duration or 0,
        }
        recipients = self.voicemail_assignee_ids or self.called_users
        recipients |= self.voicemail_box_id.member_ids
        # Skip the user who triggered this write — their UI is already up to date.
        for user in recipients.filtered(lambda u: u.id != self.env.uid):
            self.env['bus.bus']._sendone(
                'connect_actions_{}'.format(user.id),
                'voicemail_new',
                payload,
            )

    def write(self, vals):
        stage_changing = 'voicemail_stage_id' in vals
        res = super().write(vals)
        if stage_changing:
            for rec in self.filtered('voicemail_url'):
                try:
                    rec._notify_voicemail_new()
                except Exception as e:
                    logger.exception('Voicemail stage change notification error: %s', e)
        return res

    @api.model
    def _group_expand_voicemail_stage(self, stages, domain):
        return stages.search([])

    def action_assign_to_me(self):
        self.ensure_one()
        if self.env.user not in self.voicemail_assignee_ids:
            self.voicemail_assignee_ids = [(4, self.env.uid)]

    def _store_voicemail_as_attachment(self):
        """Download voicemail from Twilio and store as ir.attachment."""
        self.ensure_one()
        if not self.voicemail_url:
            return
        settings = self.env['connect.settings'].sudo()
        account_sid, auth_token = settings._get_client_credentials()
        response = requests.get(
            self.voicemail_url, auth=(account_sid, auth_token),
            timeout=HTTP_DOWNLOAD_TIMEOUT
        )
        response.raise_for_status()
        attachment = self.env['ir.attachment'].sudo().create({
            'name': 'voicemail_{}.mp3'.format(self.voicemail_sid or self.id),
            'datas': base64.b64encode(response.content).decode(),
            'res_model': self._name,
            'res_id': self.id,
            'mimetype': 'audio/mpeg',
        })
        self.write({'voicemail_attachment_id': attachment.id})
        if settings.get_param('delete_twilio_recording') and self.voicemail_sid:
            try:
                client = self.env['connect.settings'].get_client()
                if client:
                    client.recordings(self.voicemail_sid).delete()
                    logger.info('Deleted voicemail %s from Twilio', self.voicemail_sid)
            except Exception as e:
                logger.error('Failed to delete voicemail %s from Twilio: %s', self.voicemail_sid, e)
        return attachment

    def _get_voicemail_listen_url(self):
        """Return URL to include in voicemail notification emails. Override for custom storage."""
        self.ensure_one()
        if self.voicemail_attachment_id:
            return '/web/content/{}?download=false'.format(self.voicemail_attachment_id.id)
        return self.voicemail_url

    def _send_voicemail_email(self):
        """Send voicemail notification email to the intended recipient."""
        self.ensure_one()
        if not self.voicemail_url:
            return

        # Determine recipients — the user(s) who were called but didn't answer
        recipients = self.called_users or self.env['res.users']
        if self.transferred_users and not self.completed_by_user:
            recipients = self.transferred_users

        template = self.env.ref('connect.email_template_voicemail_notification')
        for user in recipients:
            connect_user = user.connect_user
            if not connect_user or not connect_user.voicemail_email_enabled:
                continue
            if not user.email:
                continue

            template.send_mail(
                self.id,
                email_layout_xmlid='mail.mail_notification_layout',
                force_send=True,
                email_values={
                    'email_to': user.email,
                    'email_from': self.env.company.email or 'noreply@example.com',
                },
            )
            logger.info('Voicemail email sent to %s for call %s', user.email, self.id)

    @api.model
    def on_call_action(self, params):
        debug(self, 'On call action: %s' % params)
        # Check if this is a Dial action webhook with transfer completion data
        if 'DialCallSid' in params and 'DialCallStatus' in params:
            try:
                self._process_transfer_completion(params)
                logger.info(f"Successfully processed transfer completion")
            except Exception as e:
                logger.error(f"Failed to process transfer completion: {e}")
        return '<Response><Hangup/></Response>'

    def _process_transfer_completion(self, params):
        """
        Process Dial action webhook to update existing transfer recipient channel.
        """
        dial_call_sid = params.get('DialCallSid')
        dial_status = params.get('DialCallStatus')
        original_call_sid = params.get('CallSid')
        original_channel = self.env['connect.channel'].search([('sid', '=', original_call_sid)], limit=1)
        if not original_channel or not original_channel.call:
            logger.warning(f"Could not find original channel for transfer CallSid: {original_call_sid}")
            return
        call = original_channel.call
        recipient_channel = None
        if call.call_pattern == 'ring_group':
            child_channels = call.channels.filtered(lambda c: c.parent_channel)
            if child_channels:
                potential_recipients = child_channels.filtered(lambda c: c.status in ['no-answer', 'ringing', 'in-progress'])
                if potential_recipients:
                    recipient_channel = potential_recipients.sorted('id', reverse=True)[0]
                else:
                    logger.error(f"No suitable recipient channels found for ring group call {call.id}")
                    return
            else:
                logger.error(f"No child channels found for ring group call {call.id}")
                return
        elif call.call_pattern == 'direct_call':
            recipient_channel = self._create_missing_transfer_channel(call, dial_call_sid, dial_status, params)
            if recipient_channel:
                logger.info(f"Created missing transfer channel {recipient_channel.id}")
            else:
                logger.error(f"Could not create missing transfer channel for call {call.id}")
                return
        else:
            logger.error(f"Cannot process transfer completion for call {call.id} with unknown pattern '{call.call_pattern}'")
            return
        if recipient_channel and recipient_channel.called_pbx_user:
            if dial_status == 'completed':
                new_status = 'completed'
                duration = int(params.get('DialCallDuration', 0))
            elif dial_status == 'busy':
                new_status = 'busy'
                duration = 0
            elif dial_status == 'no-answer':
                new_status = 'no-answer'
                duration = 0
            elif dial_status == 'failed':
                new_status = 'failed'
                duration = 0
            else:
                new_status = dial_status
                duration = int(params.get('DialCallDuration', 0))
            recipient_channel.write({
                'status': new_status,
                'duration': duration,
            })
        else:
            logger.warning(f"Could not identify transfer recipient channel or PBX user")

    def _create_missing_transfer_channel(self, call, dial_call_sid, dial_status, params):
        """Create a missing transfer channel for direct calls."""
        try:
            parent_channel = call.channels.filtered(lambda c: not c.parent_channel)
            if not parent_channel:
                logger.warning(f"No parent channel found for call {call.id}")
                return None
            parent_channel = parent_channel[0]
            target_user = None
            target_user = call.get_transfer_target(dial_call_sid)
            if not target_user:
                original_call_sid = params.get('CallSid')
                if original_call_sid:
                    target_user = call.get_transfer_target(original_call_sid)
            if not target_user:
                call_with_sudo = call.sudo()
                if call_with_sudo.transferred_users:
                    target_user = call_with_sudo.transferred_users[-1]
            if not target_user:
                logger.error(f"Cannot determine transfer target for call {call.id}")
                return None
            pbx_user = self.env['connect.user'].sudo().search([('user', '=', target_user.id)], limit=1)
            if not pbx_user:
                logger.warning(f"Could not find PBX user for {target_user.login}")
                return None
            channel_data = {
                'sid': dial_call_sid,
                'call': call.id,
                'parent_channel': parent_channel.id,
                'technical_direction': 'outbound-dial',
                'status': dial_status,
                'duration': int(params.get('DialCallDuration', 0)),
                'called_pbx_user': pbx_user.id,
                'called_user': target_user.id,
                'call_source': 'transfer',
                'caller': parent_channel.caller,
                'called': pbx_user.uri
            }
            recipient_channel = self.env['connect.channel'].create(channel_data)
            return recipient_channel
        except Exception as e:
            logger.error(f"Failed to create missing transfer channel: {e}", exc_info=True)
            return None

    def save_call_price(self, call, params):
        """Mark call as needing price fetch (will be processed by cron job)"""
        try:
            call_sid = params.get('CallSid')
            if not call_sid:
                debug(self, 'No CallSid in webhook params, cannot store for price fetching')
                return

            # Store CallSid in call record for later price fetching by cron
            call.write({
                'call_sid': call_sid,
                'is_price_fetched': False,
            })
            debug(self, f'Marked call {call.id} (CallSid: {call_sid}) for price fetching by cron job')

        except Exception as e:
            logger.error(f'Error in save_call_price: {e}')

    def _fetch_call_price_from_api(self, call, call_sid):
        """Fetch call price from Twilio REST API"""
        try:
            client = self.env['connect.settings'].get_client()
            twilio_call = client.calls(call_sid).fetch()

            debug(self, f'Fetched call data: price={twilio_call.price}, price_unit={twilio_call.price_unit}')

            if twilio_call.price is not None and twilio_call.price != '':
                # Convert price to positive float (Twilio returns negative values)
                try:
                    price_value = round(abs(float(twilio_call.price)), 3)
                    price_unit = twilio_call.price_unit or 'USD'

                    call.write({
                        'price': price_value,
                        'price_unit': price_unit,
                        'price_currency': price_unit,
                    })
                    debug(self, f'Saved call price: ${price_value:.3f} {price_unit} for call {call.id}')
                    return True

                except ValueError as e:
                    logger.error(f'Error converting call price {twilio_call.price} to float: {e}')

            else:
                debug(self, f'Call price not yet available for {call_sid}, will be available later')

        except Exception as e:
            logger.error(f'Error fetching call price from API for {call_sid}: {e}')

        return False

    @api.model
    def fetch_call_prices_batch(self):
        """Cron job method to fetch prices for calls that don't have them yet.

        Twilio pricing notes:
        - Price is "populated after the call is completed" but "may not be
          immediately available" (can take minutes to hours)
        - Zero-duration calls (never connected) will never have a price
        - Calls stuck in 'initiated' status will never have a price
        - After ~7 days, if no price is available, Twilio likely won't provide one

        See: https://www.twilio.com/docs/voice/api/call-resource
        """
        if not self.env['connect.settings'].sudo().get_param('fetch_call_prices'):
            debug(self, 'Call price fetching is disabled in settings')
            return

        # First, mark zero-duration calls as "fetched" - they'll never have prices
        # (Twilio doesn't charge for calls that never connected)
        zero_duration_calls = self.search([
            ('is_price_fetched', '=', False),
            ('call_sid', '!=', False),
            ('duration', '=', 0),
        ])
        if zero_duration_calls:
            zero_duration_calls.write({'is_price_fetched': True, 'price': 0.0})
            debug(self, f'Marked {len(zero_duration_calls)} zero-duration calls as no-charge')

        # Mark old calls (>7 days) as fetched - Twilio won't provide prices this late
        cutoff_date = fields.Datetime.now() - timedelta(days=7)
        old_calls = self.search([
            ('is_price_fetched', '=', False),
            ('call_sid', '!=', False),
            ('create_date', '<', cutoff_date),
        ])
        if old_calls:
            old_calls.write({'is_price_fetched': True})
            debug(self, f'Marked {len(old_calls)} old calls (>7 days) as expired for price fetch')

        # Find recent calls that need price fetching
        # Only fetch calls with duration > 0 that completed in last 7 days
        calls_to_fetch = self.search([
            ('is_price_fetched', '=', False),
            ('call_sid', '!=', False),
            ('status', 'in', CALL_END_STATUSES),
            ('duration', '>', 0),  # Only calls that actually connected
            ('create_date', '>=', cutoff_date),
        ])

        debug(self, f'Found {len(calls_to_fetch)} calls needing price fetch')

        for call in calls_to_fetch:
            try:
                success = self._fetch_call_price_from_api(call, call.call_sid)
                if success:
                    call.write({'is_price_fetched': True})
                    debug(self, f'Successfully fetched price for call {call.id}')
                else:
                    debug(self, f'Price not yet available for call {call.id}, will retry next time')
            except Exception as e:
                logger.error(f'Error fetching price for call {call.id}: {e}')

        debug(self, f'Batch price fetch completed')

    def _format_missed_call_message(self, channel):
        """Create a clean missed call message format."""
        caller_name = None
        caller_number = None
        if channel.call.direction == 'incoming':
            caller_number = channel.call.caller
            if channel.call.partner:
                caller_name = channel.call.partner.name
            elif channel.call.caller_user:
                caller_name = channel.call.caller_user.name
        else:
            caller_number = channel.call.called
            if channel.call.partner:
                caller_name = channel.call.partner.name
        if caller_name and caller_number:
            caller_display = f"{caller_name} ({caller_number})"
        elif caller_name:
            caller_display = caller_name
        elif caller_number:
            caller_display = caller_number
        else:
            caller_display = "Unknown"
        call_link = f" <a href='/web#id={channel.call.id}&model=connect.call&view_type=form'>Click to view the call details</a>."
        transfer_info = ""
        if channel.call.answered_user:
            transfer_info = f" Call transferred to you by {channel.call.answered_user.name}."
        body = Markup(f"You missed a call from {caller_display}.{transfer_info}{call_link}")
        subject = f"Missed call from {caller_display}"
        return subject, body

    def get_notification_users(self):
        """Gets all users who should receive missed call notifications for this call."""
        notify_users = []
        # Rule 1: called_users only (no other fields) - everyone gets notification
        if (self.called_users and
            not self.answered_user and
            not self.transferred_users and
            not self.completed_by_user):
            for user in self.called_users:
                connect_user = user.connect_user
                if connect_user and connect_user[0].missed_calls_notify:
                    notify_users.append(user)
        # Rule 3: transferred_users + NO completed_by_user - only transferred users get notification
        elif (self.transferred_users and
              not self.completed_by_user):
            for user in self.transferred_users:
                connect_user = user.connect_user
                if connect_user and connect_user[0].missed_calls_notify:
                    notify_users.append(user)
        return notify_users

    def register_call(self, channel, params):
        try:
            notify_users = []
            # Construct base message
            message = [channel.call.status.capitalize(), channel.call.direction,
                       'call at {}, '.format(channel.create_date.strftime('%Y-%m-%d %H:%M:%S'))]
            if channel.call.caller_user:
                message.append('caller: {}, '.format(channel.call.caller_user.name))
            if channel.call.duration:
                message.append('duration: {}, '.format(channel.call.duration_human))
            if channel.call.answered_user:
                message.append('answered by: {}, '.format(channel.call.answered_user.name))
            if channel.call.called_users:
                message.append('dialed users: {}, '.format(', '.join(k.name for k in channel.call.called_users)))
            # Use extracted notification method
            notify_users = channel.call.get_notification_users()
            # Register call at partner.
            if channel.call.partner:
                message.insert(3, 'partner: {}, '.format(channel.call.partner.name))
                final_message = ' '.join(message)
                if final_message.endswith(', '):
                    final_message = final_message[:-2] + '.'
                channel.call.register_call_post_message(
                    channel.call.partner, body=final_message, subtype_xmlid='mail.mt_note')
            # Send notifications if any users were identified
            if notify_users:
                logger.info(f"Sending notifications to {len(notify_users)} users: {[u.login for u in notify_users]}")
                # Deduplicate
                original_count = len(notify_users)
                notify_users = list(set(notify_users))
                if len(notify_users) < original_count:
                    logger.warning(f"Removed {original_count - len(notify_users)} duplicate users from notification list")
                debug(self, 'Missed call notification to users: {}'.format(notify_users))
                notify_subject, notify_body = self._format_missed_call_message(channel)
                channel.call.register_call_post_message(
                    channel.call,
                    subtype_xmlid='mail.mt_comment',
                    subject=notify_subject,
                    body=notify_body,
                    partner_ids=[k.partner_id.id for k in notify_users]
                )
            # Transfer context is NOT cleared here — clearing it prematurely
            # causes _create_missing_transfer_channel() to fail when late
            # webhooks arrive. The context is harmless when left around and
            # will be ignored once the call is finalized.
        except Exception as e:
            logger.exception('Register call error:', e)

    def register_call_post_message(self, obj, **kwargs):
        try:
            obj.with_user(SUPERUSER_ID).with_context(mail_create_nosubscribe=False).message_post(**kwargs)
        except Exception:
            logger.exception('Register call error: ')

    def register_summary_to_rec(self, rec, summary):
        try:
            rec.with_user(SUPERUSER_ID).message_post(body=summary)
        except Exception as e:
            logger.error('Cannot register summary: %s', e)

    @api.constrains('summary')
    def register_partner_call_summary(self):
        reload_view = False
        register_summary = self.env['connect.settings'].sudo().get_param('register_summary')
        if not register_summary:
            return
        for rec in self:
            if rec.partner and rec.summary:
                self.register_summary_to_rec(rec.partner, rec.summary)
                reload_view = True
        # Reload changed view.
        if reload_view:
            # Reload the view of res.partner
            self.env['connect.settings'].connect_reload_view('res.partner')

    def create_partner_button(self):
        self.ensure_one()
        name_number = self.caller if self.direction == 'incoming' else self.called
        context = {
            'connect_call_id': self.id,
            'default_phone': name_number,
        }
        # Check if it's a click on a call with existing partner (linking)
        if not self.partner:
            partner = self.env['res.partner'].get_partner_by_number(name_number)
            if partner:
                self.sudo().partner = partner  # Use sudo as user has not access to write to call.
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'res.partner',
            'res_id': self.partner.id,
            'name': self.partner.name if self.partner else 'New Partner',
            'view_mode': 'form',
            'target': 'current',
            'context': context,
        }

    def transfer_button(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'connect.transfer_wizard',
            'view_mode': 'form',
            'target': 'new',
            'name': 'Transfer Wizard'
        }

    @api.model
    def forward_call(self, call_sid, target):
        """Forward the current call to a target extension or phone number.

        Args:
            call_sid: The Twilio CallSid of the current user's channel
            target: Extension number (e.g., "101") or phone number (e.g., "+12025551234")

        Returns:
            dict with 'success' boolean and optional 'error' message
        """
        # Find the channel by CallSid
        channel = self.env['connect.channel'].search([('sid', '=', call_sid)], limit=1)
        if not channel:
            logger.warning('forward_call: Channel not found for CallSid %s', call_sid)
            return {'success': False, 'error': 'Channel not found'}

        call = channel.call
        if not call:
            logger.warning('forward_call: No call associated with channel %s', channel.id)
            return {'success': False, 'error': 'No call associated with channel'}

        # Get current user's connect.user
        current_user = self.env.user.connect_user
        if not current_user:
            logger.warning('forward_call: Current user has no connect.user')
            return {'success': False, 'error': 'User not configured for Connect'}

        # Find the user's channel and other party's channel
        user_channel = call.channels.filtered(
            lambda x: x.caller_pbx_user == current_user or x.called_pbx_user == current_user
        )
        if not user_channel:
            logger.warning('forward_call: Cannot find user channel for user %s in call %s',
                          current_user.name, call.id)
            return {'success': False, 'error': 'User channel not found'}

        # Take the first one if multiple (shouldn't happen normally)
        user_channel = user_channel[0]

        other_channels = call.channels - user_channel
        if not other_channels:
            logger.warning('forward_call: No other channels to forward in call %s', call.id)
            return {'success': False, 'error': 'No other party to forward'}

        # Take the primary other channel (the external party)
        other_channel = other_channels[0]

        # Determine if target is an extension or phone number
        target_user = None
        if target and not target.startswith('+') and len(target) <= 5:
            # Likely an extension number
            target_user = self.env['connect.user'].search([('exten_number', '=', target)], limit=1)

        client = self.env['connect.settings'].get_client()
        api_url = self.env['connect.settings'].sudo().get_param('api_url')
        edge = self.env['connect.settings'].sudo().get_param('twilio_edge')
        conf_id = uuid.uuid4().hex
        conf_name = 'forward-{}-{}'.format(call.id, conf_id)

        # Check if recording is enabled for the current user
        record_calls = current_user.record_calls if current_user else False
        recording_url = '{}/twilio/webhook/recordingstatus#e={}'.format(
            api_url.rstrip('/'), edge) if record_calls else None

        try:
            # Step 1: Put the other party into a conference (with hold music)
            response_other = VoiceResponse()
            self.tts_system_message(response_other, 'system.transfer')
            dial_conf = Dial()
            conf_kwargs = {
                'startConferenceOnEnter': True,
                'endConferenceOnExit': True,
                'waitUrl': call._get_hold_music_url(),
                'record': 'record-from-start' if record_calls else 'do-not-record',
            }
            if recording_url:
                conf_kwargs['recordingStatusCallback'] = recording_url
                conf_kwargs['recordingStatusCallbackEvent'] = 'completed'
            dial_conf.conference(conf_name, **conf_kwargs)
            response_other.append(dial_conf)
            client.calls(other_channel.sid).update(twiml=str(response_other))
            logger.info('forward_call: Put channel %s into conference %s', other_channel.sid, conf_name)

            # Step 2: Dial the target into the same conference
            if target_user:
                # Dial the extension - render TwiML to ring their devices
                # Use the user's render method to create proper dial TwiML
                response_target = VoiceResponse()
                status_url = '{}/twilio/webhook/callstatus#e={}'.format(api_url.rstrip('/'), edge)

                # Dial into the conference after the target answers
                # We need to create an outbound call that joins the conference
                dial_target = Dial()
                dial_target.conference(
                    conf_name,
                    startConferenceOnEnter=True,
                    endConferenceOnExit=True
                )
                response_target.append(dial_target)

                # Create outbound call to the target user
                # Determine caller ID for the forwarded call
                caller_id = other_channel.caller_number or call.caller or current_user.outgoing_callerid

                # Create call to target user's client
                target_identity = target_user.get_client_identity()
                client.calls.create(
                    to='client:{}'.format(target_identity),
                    from_=caller_id,
                    twiml=str(response_target),
                    status_callback=status_url,
                    status_callback_event=['initiated', 'answered', 'completed']
                )
                logger.info('forward_call: Dialing target user %s (client:%s) into conference %s',
                           target_user.name, target_identity, conf_name)
            else:
                # Target is a phone number - dial it directly into conference
                target_number = target if target.startswith('+') else '+{}'.format(target)
                response_target = VoiceResponse()
                dial_target = Dial()
                dial_target.conference(
                    conf_name,
                    startConferenceOnEnter=True,
                    endConferenceOnExit=True
                )
                response_target.append(dial_target)

                caller_id = other_channel.caller_number or call.caller or current_user.outgoing_callerid
                status_url = '{}/twilio/webhook/callstatus#e={}'.format(api_url.rstrip('/'), edge)

                client.calls.create(
                    to=target_number,
                    from_=caller_id,
                    twiml=str(response_target),
                    status_callback=status_url,
                    status_callback_event=['initiated', 'answered', 'completed']
                )
                logger.info('forward_call: Dialing target number %s into conference %s',
                           target_number, conf_name)

            # Step 3: Hang up the current user's leg
            client.calls(user_channel.sid).update(status='completed')
            logger.info('forward_call: Hung up user channel %s', user_channel.sid)

            return {'success': True, 'conference': conf_name}

        except Exception as e:
            logger.exception('forward_call: Error forwarding call %s to %s', call.id, target)
            return {'success': False, 'error': str(e)}

    def _get_hold_music_url(self):
        """Return the hold music URL. Centralized for easy configuration later."""
        return 'http://twimlets.com/holdmusic?Bucket=com.twilio.music.classical'

    # -------------------------------------------------------------------------
    # Conference Call Control: Hold, Transfer, Add Participant, Merge
    # -------------------------------------------------------------------------

    def _find_call_channels(self, call_sid):
        """Find call, user channel, and other channel from a CallSid.
        Returns (call, user_channel, other_channel) or raises."""
        channel = self.env['connect.channel'].search([('sid', '=', call_sid)], limit=1)
        if not channel:
            return None, None, None
        call = channel.call
        if not call:
            return None, None, None
        current_user = self.env.user.connect_user
        if not current_user:
            return call, None, None
        user_channel = call.channels.filtered(
            lambda x: x.caller_pbx_user == current_user or x.called_pbx_user == current_user
        )
        if not user_channel:
            return call, None, None
        user_channel = user_channel[0]
        other_channels = call.channels - user_channel
        other_channel = other_channels[0] if other_channels else None
        return call, user_channel, other_channel

    def _acquire_call_lock(self):
        """Acquire exclusive row lock for call control operations."""
        self.env.cr.execute(
            "SELECT id FROM connect_call WHERE id = %s FOR UPDATE NOWAIT",
            (self.id,)
        )

    @api.model
    def promote_to_conference(self, call_sid):
        """Promote a peer-to-peer call to a Twilio Conference for advanced call control.

        Both parties are moved into a named conference room. The user gets
        endConferenceOnExit=True (leaving cleans up), the remote party gets
        endConferenceOnExit=False (so hold/transfer don't kill the call).

        Returns dict with success, conference_sid, conference_name.
        """
        call, user_channel, other_channel = self._find_call_channels(call_sid)
        if not call:
            return {'success': False, 'error': 'Call not found'}
        if not user_channel or not other_channel:
            return {'success': False, 'error': 'Cannot identify call parties'}

        # Already promoted
        if call.conference_name:
            return {
                'success': True,
                'conference_sid': call.conference_sid,
                'conference_name': call.conference_name,
            }

        client = self.env['connect.settings'].get_client()
        api_url = self.env['connect.settings'].sudo().get_param('api_url')
        edge = self.env['connect.settings'].sudo().get_param('twilio_edge')
        conf_name = 'callcontrol-{}-{}'.format(call.id, uuid.uuid4().hex)
        status_url = '{}/twilio/webhook/conference_event#e={}'.format(
            api_url.rstrip('/'), edge)

        # Check if recording is enabled for the current user
        current_user = self.env.user.connect_user
        record_calls = current_user.record_calls if current_user else False
        recording_url = '{}/twilio/webhook/recordingstatus#e={}'.format(
            api_url.rstrip('/'), edge) if record_calls else None

        try:
            # Put remote party into conference (endConferenceOnExit=False so hold works)
            response_other = VoiceResponse()
            dial_other = Dial()
            conf_kwargs = {
                'startConferenceOnEnter': True,
                'endConferenceOnExit': False,
                'beep': False,
                'waitUrl': call._get_hold_music_url(),
                'statusCallback': status_url,
                'statusCallbackEvent': 'join leave end',
                'record': 'record-from-start' if record_calls else 'do-not-record',
            }
            if recording_url:
                conf_kwargs['recordingStatusCallback'] = recording_url
                conf_kwargs['recordingStatusCallbackEvent'] = 'completed'
            dial_other.conference(conf_name, **conf_kwargs)
            response_other.append(dial_other)
            client.calls(other_channel.sid).update(twiml=str(response_other))

            # Put user into conference (endConferenceOnExit=True for cleanup)
            response_user = VoiceResponse()
            dial_user = Dial()
            dial_user.conference(
                conf_name,
                startConferenceOnEnter=True,
                endConferenceOnExit=True,
                beep=False,
            )
            response_user.append(dial_user)
            client.calls(user_channel.sid).update(twiml=str(response_user))

            # Try once to get SID; don't block the worker if not available yet.
            # The conference_name is the primary identifier and is always set.
            # _ensure_conference_sid() will lazily resolve the SID when needed.
            try:
                conferences = client.conferences.list(
                    friendly_name=conf_name, status='in-progress', limit=1)
                conf_sid = conferences[0].sid if conferences else None
            except Exception as e:
                logger.warning('promote_to_conference: Could not fetch SID for %s: %s', conf_name, e)
                conf_sid = None

            call.write({
                'conference_sid': conf_sid,
                'conference_name': conf_name,
            })
            logger.info('promote_to_conference: Call %s promoted to conference %s (SID: %s)',
                        call.id, conf_name, conf_sid)
            return {
                'success': True,
                'conference_sid': conf_sid,
                'conference_name': conf_name,
            }

        except Exception as e:
            logger.exception('promote_to_conference: Failed for call %s', call.id)
            return {'success': False, 'error': str(e)}

    def _get_conference_participant(self, client, conf_sid, target_call_sid):
        """Find a conference participant by their call SID."""
        try:
            participants = client.conferences(conf_sid).participants.list()
            for p in participants:
                if p.call_sid == target_call_sid:
                    return p
            return None
        except Exception as e:
            logger.warning('_get_conference_participant: Failed for conf %s, call %s: %s',
                           conf_sid, target_call_sid, e)
            return None

    def _ensure_conference_sid(self, client):
        """Lazily resolve and cache the conference SID from the conference name.

        Called before any operation that requires conference_sid. If the SID
        was not available when promote_to_conference ran (Twilio hadn't created
        the conference yet), this retries with exponential backoff (up to ~3s
        total) before giving up.
        """
        self.ensure_one()
        if self.conference_sid:
            return self.conference_sid
        if not self.conference_name:
            return None
        # Retry with exponential backoff: 0.25s, 0.5s, 1.0s, 2.0s, 3.0s (~6.75s total)
        delays = [0.25, 0.5, 1.0, 2.0, 3.0]
        for attempt, delay in enumerate(delays, 1):
            try:
                conferences = client.conferences.list(
                    friendly_name=self.conference_name, status='in-progress', limit=1)
                if conferences:
                    self.conference_sid = conferences[0].sid
                    logger.info('_ensure_conference_sid: Resolved SID %s for conference %s (attempt %d)',
                                self.conference_sid, self.conference_name, attempt)
                    return self.conference_sid
            except Exception as e:
                logger.warning('_ensure_conference_sid: Attempt %d failed for %s: %s',
                               attempt, self.conference_name, e)
            if attempt < len(delays):
                time.sleep(delay)
        logger.warning('_ensure_conference_sid: Could not resolve SID for %s after %d attempts',
                       self.conference_name, len(delays))
        return None

    @api.model
    def hold_call(self, call_sid):
        """Put the remote party on hold with hold music."""
        call, user_channel, other_channel = self._find_call_channels(call_sid)
        if not call:
            return {'success': False, 'error': 'Call not found'}
        try:
            call._acquire_call_lock()
        except OperationalError:
            self.env.cr.rollback()
            return {'success': False, 'error': 'Call is being modified by another operation, please try again'}
        if not other_channel:
            return {'success': False, 'error': 'No remote party found'}

        # Promote to conference if not already
        if not call.conference_name:
            result = self.promote_to_conference(call_sid)
            if not result.get('success'):
                return result
            call.invalidate_recordset(['conference_sid', 'conference_name'])

        client = self.env['connect.settings'].get_client()
        conf_sid = call._ensure_conference_sid(client)
        if not conf_sid:
            return {'success': False, 'error': 'Conference SID not available'}
        try:
            participant = self._get_conference_participant(
                client, conf_sid, other_channel.sid)
            if not participant:
                return {'success': False, 'error': 'Remote party not in conference'}

            client.conferences(conf_sid).participants(
                participant.call_sid
            ).update(
                hold=True,
                hold_url=call._get_hold_music_url(),
            )
            call.is_on_hold = True
            logger.info('hold_call: Call %s remote party on hold', call.id)
            return {'success': True}

        except Exception as e:
            logger.exception('hold_call: Failed for call %s', call.id)
            return {'success': False, 'error': str(e)}

    @api.model
    def resume_call(self, call_sid):
        """Resume the remote party from hold."""
        call, user_channel, other_channel = self._find_call_channels(call_sid)
        if not call:
            return {'success': False, 'error': 'Call not found'}
        try:
            call._acquire_call_lock()
        except OperationalError:
            self.env.cr.rollback()
            return {'success': False, 'error': 'Call is being modified by another operation, please try again'}
        if not call.conference_name:
            return {'success': False, 'error': 'Call not in conference mode'}
        if not other_channel:
            return {'success': False, 'error': 'No remote party found'}

        client = self.env['connect.settings'].get_client()
        conf_sid = call._ensure_conference_sid(client)
        if not conf_sid:
            return {'success': False, 'error': 'Conference SID not available'}
        try:
            participant = self._get_conference_participant(
                client, conf_sid, other_channel.sid)
            if not participant:
                return {'success': False, 'error': 'Remote party not in conference'}

            client.conferences(conf_sid).participants(
                participant.call_sid
            ).update(hold=False)
            call.is_on_hold = False
            logger.info('resume_call: Call %s remote party resumed', call.id)
            return {'success': True}

        except Exception as e:
            logger.exception('resume_call: Failed for call %s', call.id)
            return {'success': False, 'error': str(e)}

    @api.model
    def get_recording_state(self, call_sid):
        """Return live recording state for an active call by querying Twilio.

        Checks conference, child leg, and parent leg (record_calls dial-level recordings
        live on the parent call SID, not the browser client SID).
        """
        channel = self.env['connect.channel'].search([('sid', '=', call_sid)], limit=1)
        if not channel or not channel.call:
            return {'success': False, 'error': 'Call not found'}
        call = channel.call
        connect_user = self.env.user.connect_user
        can_record = bool(connect_user and connect_user.record_calls)
        is_conference = bool(call.conference_name)
        not_recording = {
            'success': True, 'is_recording': False, 'is_paused': False,
            'recording_sid': None, 'recording_call_sid': None,
            'is_conference': is_conference, 'can_record': can_record,
        }
        try:
            client = self.env['connect.settings'].get_client()

            def _find_active(recs):
                return next((r for r in recs if r.status in ('in-progress', 'paused')), None)

            active = None
            ctrl_sid = None

            if is_conference:
                conf_sid = call._ensure_conference_sid(client)
                if conf_sid:
                    active = _find_active(client.conferences(conf_sid).recordings.list())
                    ctrl_sid = conf_sid

            if not active:
                try:
                    active = _find_active(client.calls(call_sid).recordings.list())
                    ctrl_sid = call_sid
                except Exception:
                    pass

            if not active and channel.parent_sid:
                # Auto-recordings via record-from-answer-dual live on the parent leg
                try:
                    active = _find_active(client.calls(channel.parent_sid).recordings.list())
                    ctrl_sid = channel.parent_sid
                except Exception:
                    pass

            if not active:
                return not_recording
            return {
                'success': True,
                'is_recording': active.status == 'in-progress',
                'is_paused': active.status == 'paused',
                'recording_sid': active.sid,
                'recording_call_sid': ctrl_sid,
                'is_conference': is_conference,
                'can_record': can_record,
            }
        except Exception as e:
            logger.exception('get_recording_state: Failed for call %s', call_sid)
            return {'success': False, 'error': str(e)}

    @api.model
    def toggle_recording(self, call_sid, recording_sid=None, action=None, recording_call_sid=None):
        """Control recording on an active call.

        action: 'start' | 'pause' | 'resume' | 'stop'
        recording_sid: SID of the recording to control (required for pause/resume/stop)
        recording_call_sid: Call or conference SID that owns the recording; needed when
            the recording lives on a different leg (e.g. parent call for record_calls auto-recordings)
        Completed recordings land in connect.recording via the status callback.
        """
        call, user_channel, other_channel = self._find_call_channels(call_sid)
        if not call:
            return {'success': False, 'error': 'Call not found'}

        # Legacy callers pass no action — infer from recording_sid presence
        if action is None:
            action = 'stop' if recording_sid else 'start'

        client = self.env['connect.settings'].get_client()
        api_url = self.env['connect.settings'].sudo().get_param('api_url')
        edge = self.env['connect.settings'].sudo().get_param('twilio_edge')
        status_callback = '{}/twilio/webhook/recordingstatus#e={}'.format(
            api_url.rstrip('/'), edge)

        try:
            if action == 'start':
                if call.conference_name:
                    conf_sid = call._ensure_conference_sid(client)
                    if not conf_sid:
                        return {'success': False, 'error': 'Conference SID not available'}
                    recording = client.conferences(conf_sid).recordings.create(
                        recording_status_callback=status_callback,
                        recording_status_callback_event=['completed'],
                    )
                    ctrl_sid = conf_sid
                else:
                    recording = client.calls(call_sid).recordings.create(
                        recording_status_callback=status_callback,
                        recording_status_callback_event=['completed'],
                    )
                    ctrl_sid = call_sid
                logger.info('toggle_recording: Started %s on call %s', recording.sid, call.id)
                return {
                    'success': True, 'recording_sid': recording.sid,
                    'recording_call_sid': ctrl_sid, 'is_recording': True, 'is_paused': False,
                }

            # pause / resume / stop
            if not recording_sid:
                return {'success': False, 'error': 'recording_sid required'}

            if action == 'stop' and call.conference_name:
                return {'success': False, 'error': 'Conference recordings cannot be stopped; use pause'}

            status_map = {'pause': 'paused', 'resume': 'in-progress', 'stop': 'stopped'}
            if action not in status_map:
                return {'success': False, 'error': 'Unknown action: {}'.format(action)}
            new_status = status_map[action]

            ctrl_sid = recording_call_sid or call_sid
            if call.conference_name:
                conf_sid = call._ensure_conference_sid(client)
                if not conf_sid:
                    return {'success': False, 'error': 'Conference SID not available'}
                client.conferences(conf_sid).recordings(recording_sid).update(status=new_status)
            else:
                client.calls(ctrl_sid).recordings(recording_sid).update(status=new_status)

            logger.info('toggle_recording: %s %s on call %s', action, recording_sid, call.id)
            if action == 'stop':
                return {'success': True, 'recording_sid': None, 'recording_call_sid': None,
                        'is_recording': False, 'is_paused': False}
            elif action == 'pause':
                return {'success': True, 'recording_sid': recording_sid, 'recording_call_sid': ctrl_sid,
                        'is_recording': False, 'is_paused': True}
            else:  # resume
                return {'success': True, 'recording_sid': recording_sid, 'recording_call_sid': ctrl_sid,
                        'is_recording': True, 'is_paused': False}

        except Exception as e:
            logger.exception('toggle_recording: Failed for call %s', call.id)
            return {'success': False, 'error': str(e)}

    @api.model
    def add_conference_participant(self, call_sid, target):
        """Add a new participant to the call's conference.

        Args:
            call_sid: The user's current CallSid
            target: Extension number or phone number to add
        """
        call, user_channel, other_channel = self._find_call_channels(call_sid)
        if not call:
            return {'success': False, 'error': 'Call not found'}
        try:
            call._acquire_call_lock()
        except OperationalError:
            self.env.cr.rollback()
            return {'success': False, 'error': 'Call is being modified by another operation, please try again'}

        # Promote to conference if not already
        if not call.conference_name:
            result = self.promote_to_conference(call_sid)
            if not result.get('success'):
                return result
            call.invalidate_recordset(['conference_sid', 'conference_name'])

        client = self.env['connect.settings'].get_client()
        conf_sid = call._ensure_conference_sid(client)
        if not conf_sid:
            return {'success': False, 'error': 'Conference SID not available'}
        api_url = self.env['connect.settings'].sudo().get_param('api_url')
        edge = self.env['connect.settings'].sudo().get_param('twilio_edge')
        current_user = self.env.user.connect_user
        status_url = '{}/twilio/webhook/callstatus#e={}'.format(api_url.rstrip('/'), edge)

        # Determine caller ID
        caller_id = None
        if other_channel:
            caller_id = other_channel.caller_number or call.caller
        if not caller_id and current_user:
            caller_id = current_user.outgoing_callerid
        if not caller_id:
            caller_id = self.env['connect.settings'].sudo().get_param('default_caller_id')

        # Resolve target
        target_user = None
        if target and not target.startswith('+') and len(target) <= 5:
            target_user = self.env['connect.user'].search(
                [('exten_number', '=', target)], limit=1)

        try:
            if target_user:
                target_identity = target_user.get_client_identity()
                to_param = 'client:{}'.format(target_identity)
            else:
                to_param = target if target.startswith('+') else '+{}'.format(target)

            # Add participant via Conference Participant API
            client.conferences(conf_sid).participants.create(
                from_=caller_id,
                to=to_param,
                end_conference_on_exit=False,
                beep=False,
                status_callback=status_url,
                status_callback_event='initiated ringing answered completed',
            )
            logger.info('add_conference_participant: Added %s to conference %s',
                        target, call.conference_name)
            return {'success': True}

        except Exception as e:
            logger.exception('add_conference_participant: Failed for call %s', call.id)
            return {'success': False, 'error': str(e)}

    @api.model
    def initiate_attended_transfer(self, call_sid):
        """Begin attended transfer: put remote party on hold so user can consult.

        The user stays connected and can dial the consult target from the phone UI.
        """
        result = self.hold_call(call_sid)
        if not result.get('success'):
            return result
        logger.info('initiate_attended_transfer: Remote party on hold for call_sid %s', call_sid)
        return {'success': True}

    @api.model
    def complete_attended_transfer(self, call_sid, consult_target):
        """Complete attended transfer: connect held caller with consult target, remove user.

        Args:
            call_sid: The user's CallSid (in the original call)
            consult_target: Extension or phone number the user consulted with
        """
        call, user_channel, other_channel = self._find_call_channels(call_sid)
        if not call:
            return {'success': False, 'error': 'Call not found'}
        try:
            call._acquire_call_lock()
        except OperationalError:
            self.env.cr.rollback()
            return {'success': False, 'error': 'Call is being modified by another operation, please try again'}
        if not call.conference_name:
            return {'success': False, 'error': 'Call not in conference mode'}

        client = self.env['connect.settings'].get_client()
        conf_sid = call._ensure_conference_sid(client)
        if not conf_sid:
            return {'success': False, 'error': 'Conference SID not available'}
        api_url = self.env['connect.settings'].sudo().get_param('api_url')
        edge = self.env['connect.settings'].sudo().get_param('twilio_edge')
        current_user = self.env.user.connect_user
        status_url = '{}/twilio/webhook/callstatus#e={}'.format(api_url.rstrip('/'), edge)

        # Determine caller ID
        caller_id = None
        if other_channel:
            caller_id = other_channel.caller_number or call.caller
        if not caller_id and current_user:
            caller_id = current_user.outgoing_callerid
        if not caller_id:
            caller_id = self.env['connect.settings'].sudo().get_param('default_caller_id')

        # Resolve consult target
        target_user = None
        if consult_target and not consult_target.startswith('+') and len(consult_target) <= 5:
            target_user = self.env['connect.user'].search(
                [('exten_number', '=', consult_target)], limit=1)

        try:
            if target_user:
                target_identity = target_user.get_client_identity()
                to_param = 'client:{}'.format(target_identity)
            else:
                to_param = consult_target if consult_target.startswith('+') else '+{}'.format(consult_target)

            # Add consult target to conference with endConferenceOnExit=True
            # so the call ends cleanly when the last real party hangs up
            client.conferences(conf_sid).participants.create(
                from_=caller_id,
                to=to_param,
                end_conference_on_exit=True,
                beep=False,
                status_callback=status_url,
                status_callback_event='initiated ringing answered completed',
            )

            # Resume the held party
            if call.is_on_hold and other_channel:
                participant = self._get_conference_participant(
                    client, conf_sid, other_channel.sid)
                if participant:
                    client.conferences(conf_sid).participants(
                        participant.call_sid).update(hold=False)
                    call.is_on_hold = False

            # Remove user from conference with retry to prevent eavesdropping
            user_removed = False
            if user_channel:
                for attempt in range(1, 4):
                    try:
                        user_participant = self._get_conference_participant(
                            client, conf_sid, user_channel.sid)
                        if user_participant:
                            client.conferences(conf_sid).participants(
                                user_participant.call_sid).update(status='completed')
                            user_removed = True
                            break
                    except Exception as e:
                        logger.warning('complete_attended_transfer: Attempt %d to remove user from conf %s failed: %s',
                                       attempt, conf_sid, e)
                    if attempt < 3:
                        time.sleep(0.5)

                if not user_removed:
                    # Fallback: terminate user's call leg directly
                    try:
                        client.calls(user_channel.sid).update(status='completed')
                        user_removed = True
                    except Exception as e:
                        logger.error('complete_attended_transfer: Fallback termination of user leg %s failed: %s',
                                     user_channel.sid, e)

                if not user_removed:
                    # Nuclear option: terminate conference to prevent eavesdropping
                    logger.error('complete_attended_transfer: PRIVACY BREACH PREVENTION - '
                                 'terminating conference %s because user could not be removed', conf_sid)
                    try:
                        client.conferences(conf_sid).update(status='completed')
                    except Exception as e:
                        logger.error('complete_attended_transfer: Failed to terminate conference %s: %s',
                                     conf_sid, e)

            # Track transfer
            if target_user and target_user.user:
                call.add_transferred_user(target_user.user)
                call.completed_by_user = target_user.user

            logger.info('complete_attended_transfer: Call %s transferred to %s',
                        call.id, consult_target)
            return {'success': True}

        except Exception as e:
            logger.exception('complete_attended_transfer: Failed for call %s', call.id)
            return {'success': False, 'error': str(e)}

    @api.model
    def cancel_attended_transfer(self, call_sid):
        """Cancel attended transfer: resume the held caller."""
        result = self.resume_call(call_sid)
        if not result.get('success'):
            return result
        logger.info('cancel_attended_transfer: Resumed caller for call_sid %s', call_sid)
        return {'success': True}

    @api.model
    def merge_calls(self, primary_call_sid, secondary_call_sid):
        """Merge two calls by moving secondary call's participants into primary's conference.

        Args:
            primary_call_sid: CallSid of the primary call (will keep its conference)
            secondary_call_sid: CallSid of the call to merge in
        """
        primary_call, primary_user_ch, primary_other_ch = self._find_call_channels(primary_call_sid)
        secondary_call, secondary_user_ch, secondary_other_ch = self._find_call_channels(secondary_call_sid)

        if not primary_call or not secondary_call:
            return {'success': False, 'error': 'One or both calls not found'}

        # Promote primary to conference if not already
        if not primary_call.conference_name:
            result = self.promote_to_conference(primary_call_sid)
            if not result.get('success'):
                return result
            primary_call.invalidate_recordset(['conference_sid', 'conference_name'])

        client = self.env['connect.settings'].get_client()
        conf_sid = primary_call._ensure_conference_sid(client)
        if not conf_sid:
            return {'success': False, 'error': 'Conference SID not available'}
        api_url = self.env['connect.settings'].sudo().get_param('api_url')
        edge = self.env['connect.settings'].sudo().get_param('twilio_edge')
        current_user = self.env.user.connect_user
        caller_id = current_user.outgoing_callerid if current_user else None
        if not caller_id:
            caller_id = self.env['connect.settings'].sudo().get_param('default_caller_id')

        try:
            # Move secondary's remote party into primary conference
            if secondary_other_ch:
                response = VoiceResponse()
                dial = Dial()
                dial.conference(
                    primary_call.conference_name,
                    startConferenceOnEnter=True,
                    endConferenceOnExit=False,
                    beep=False,
                )
                response.append(dial)
                client.calls(secondary_other_ch.sid).update(twiml=str(response))

            logger.info('merge_calls: Merged call %s into call %s conference %s',
                        secondary_call.id, primary_call.id, primary_call.conference_name)
            return {'success': True}

        except Exception as e:
            logger.exception('merge_calls: Failed merging call %s into %s',
                             secondary_call.id, primary_call.id)
            return {'success': False, 'error': str(e)}

    @api.model
    def on_conference_event(self, params):
        """Handle Twilio conference status callback events."""
        event = params.get('StatusCallbackEvent', '')
        conf_name = params.get('FriendlyName', '')
        conf_sid = params.get('ConferenceSid', '')

        if not conf_name.startswith('callcontrol-'):
            return '<Response/>'

        if event == 'conference-end':
            call = self.search([('conference_name', '=', conf_name)], limit=1)
            if call:
                call.write({
                    'conference_sid': False,
                    'conference_name': False,
                    'is_on_hold': False,
                })
                logger.info('on_conference_event: Conference %s ended, cleaned up call %s',
                            conf_name, call.id)

        elif event == 'participant-leave':
            # Check if only one participant remains — if so, end conference
            call = self.search([('conference_name', '=', conf_name)], limit=1)
            if call and (call.conference_sid or conf_sid):
                client = self.env['connect.settings'].get_client()
                try:
                    participants = client.conferences(conf_sid).participants.list()
                    if len(participants) <= 1:
                        # End conference — last party shouldn't be left alone
                        client.conferences(conf_sid).update(status='completed')
                        logger.info('on_conference_event: Ended conference %s (last participant)',
                                    conf_name)
                except Exception as e:
                    logger.warning('on_conference_event: Error checking participants: %s', e)

        return '<Response/>'

    def redial(self):
        self.ensure_one()
        self.env['connect.settings'].originate_call(
            number=self.called if self.direction == 'outgoing' else self.caller,
        )

    @api.model
    def get_widget_calls(self, domain, limit=None, offset=0, order='id desc', fields=[]):
        calls = self.search(domain, offset, limit, order)
        payload = []
        read_fields = self.get_widget_fields()
        if isinstance(fields, list):
            read_fields.extend(fields)
        for call in calls:
            call_data = call.read(read_fields)[0]
            if call.called_users:
                call_data.update({'called_users': list(call.called_users.read(['id', 'name'])[0].values())})
            # Add notification users for phone UI highlighting
            notification_users = call.get_notification_users()
            call_data.update({'notification_user_ids': [user.id for user in notification_users]})
            payload.append(call_data)
        return payload

    def get_widget_fields(self):
        return [
            "id",
            "called",
            "caller",
            "caller_user",
            "called_users",
            "partner",
            "create_date",
            "direction",
            "status",
            "answered_user",
            "completed_by_user",
            "transferred_users",
            "call_pattern"
        ]
