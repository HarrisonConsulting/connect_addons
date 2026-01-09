# -*- coding: utf-8 -*-

import json
import logging
import re
from urllib.parse import urljoin
import uuid
from datetime import timedelta
from odoo import fields, models, api, release, SUPERUSER_ID, tools
from odoo.exceptions import ValidationError
from twilio.twiml.voice_response import VoiceResponse, Say, Dial, Conference, Client, Number, Sip
from .settings import debug

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
    if release.version_info[0] >= 17.0:
        recording_widget = fields.Html(compute='_get_recording_data', sanitize=False)
    else:
        recording_widget = fields.Char(compute='_get_recording_data')
    recording_icon = fields.Html(compute='_get_recording_data', string='R')
    summary = fields.Html()
    called = fields.Char(readonly=True)
    caller = fields.Char(readonly=True)
    parent_call = fields.Many2one('connect.call', ondelete='cascade', readonly=True)
    partner = fields.Many2one('res.partner', ondelete='set null')
    partner_img = fields.Binary(related='partner.image_1920', string='Partner Image')
    direction = fields.Char(index=True, readonly=True)
    call_type = fields.Selection([
        ('phone', 'Phone'),
        ('whatsapp', 'WhatsApp')
    ], default='phone', index=True)
    status = fields.Char(readonly=True)
    duration = fields.Integer(string='Seconds', readonly=True)
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
    # Scheduled fields.
    scheduled_datetime = fields.Datetime()
    # Voicemail fields
    voicemail_url = fields.Char(readonly=True)
    voicemail_duration = fields.Integer(readonly=True)
    voicemail_icon = fields.Html(compute='_get_voicemail_icon', string='V', store=True)
    if release.version_info[0] >= 17.0:
        voicemail_widget = fields.Html(compute='_get_voicemail_widget', string='VoiceMail', sanitize=False)
    else:
        voicemail_widget = fields.Char(compute='_get_voicemail_widget', string='VoiceMail')
    # Reference, to submit call history and summary.
    ref = fields.Reference(selection=[('res.partner', 'Partner')], compute='_get_ref')
    has_error = fields.Boolean(index=True)
    error_code = fields.Char(readonly=True)
    error_message = fields.Text(readonly=True)
    # Call price fields
    price = fields.Float(string='Call Price', readonly=True, digits=(10, 3))
    price_unit = fields.Char(string='Price Unit', readonly=True, help='The currency unit for call price (e.g., USD)')
    price_currency = fields.Char(string='Price Currency', readonly=True, default='USD')
    call_sid = fields.Char(string='Twilio Call SID', readonly=True, help='Twilio CallSid for fetching price information')
    is_price_fetched = fields.Boolean(string='Price Fetched', default=False, readonly=True, help='Indicates if call price has been fetched from Twilio API')

    def _get_name(self):
        for rec in self:
            try:
                started = fields.Datetime.context_timestamp(rec, rec.create_date)
                formatted_time = fields.Datetime.to_string(started)
                rec.name = '{} {} call at {}'.format(rec.status, rec.direction, formatted_time).capitalize()
            except Exception:
                logger.exception('Call name compute error:')
                # Show just call ID if we failed to render the name above.
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
                rec.recording_icon = '<span class="fa fa-file-sound-o"/>'
                rec.recording_widget = recording.recording_widget
            else:
                rec.recording_icon = ''
                rec.transcript = ''
                rec.recording = False
                rec.recording_widget = ''

    def _get_voicemail_widget(self):
        proxy_recordings = self.env['connect.settings'].sudo().get_param('proxy_recordings')
        for rec in self:
            if rec.voicemail_url:
                if proxy_recordings:
                    media_url = '/connect/voicemail/{}'.format(rec.id)
                else:
                    media_url = rec.voicemail_url
                rec.voicemail_widget = '<audio id="sound_file" preload="auto" ' \
                    'controls="controls"> ' \
                    '<source src="{}"/>' \
                    '</audio>'.format(media_url)
            else:
                rec.voicemail_widget = ''

    @api.depends('voicemail_url')
    def _get_voicemail_icon(self):
        for rec in self:
            if rec.voicemail_url:
                rec.voicemail_icon = '<span class="fa fa-envelope-o"/>'
            else:
                rec.voicemail_icon = ''

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

    def write(self, vals):
        return super().write(vals)

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

    @api.model
    def on_call_status(self, params):
        self = self.sudo()
        # Create channel
        channel = self.env['connect.channel'].on_call_status(params)
        if not channel:
            logger.error('No channel returned from on_call_status!')
            return False
        if not channel.parent_channel and not channel.call:
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
            call_vals = {
                'partner': channel.partner.id,
                'called': channel.called_number,
                'caller': channel.caller_number,
                'status': channel.status,
                'caller_pbx_user': channel.caller_pbx_user.id,
                'caller_user': channel.caller_user.id,
                'direction': direction,
                'call_type': channel.call_type or 'phone',
            }
            # Set parent_call if in queue context (agent dial linked to customer call)
            parent_call_id = self.env.context.get('queue_parent_call_id')
            if parent_call_id:
                call_vals['parent_call'] = parent_call_id
            call = self.with_context(tracking_disable=True).create(call_vals)
            channel.call = call
        elif channel.parent_channel and channel.parent_channel.call:
            # Secondary channel, assign the call from the parent.
            channel.call = channel.parent_channel.call
            if channel.caller_pbx_user and channel.parent_channel.called_pbx_user:
                channel.call.direction = 'internal'
            elif channel.called_pbx_user and channel.parent_channel.caller_pbx_user:
                channel.call.direction = 'internal'
        # Determine call status using priority-based resolution.
        # When multiple users are dialed, we need to determine the ONE final outcome.
        # Priority (highest wins): answered > voicemail > rejected > busy > missed > failed
        child_channels = [ch for ch in channel.call.channels if ch.parent_channel]

        if child_channels:
            # Resolve status from child channels using priority
            channel.call.status, completed_child = self._resolve_call_status(child_channels)
            # Set answered user if someone answered
            if completed_child and completed_child.called_pbx_user:
                channel.call.answered_pbx_user = completed_child.called_pbx_user
                channel.call.answered_user = completed_child.called_pbx_user.user
        else:
            # No children (single-channel call, e.g., direct SIP)
            # Map Twilio status to our business status
            channel.call.status = self._map_twilio_status(channel.status)
        # Set call duration from the first channel (parent/inbound)
        channel.call.duration = channel.call.channels.sorted(key='id', reverse=False)[0].duration
        # Set called from 2nd call leg for click2call external calls.
        if channel.parent_channel.technical_direction == 'outbound-api':
            channel.call.called = channel.called_number
        # Set called users (avoid duplicates to prevent serialization errors)
        if channel.called_user and channel.called_user not in channel.call.called_users:
            channel.call.called_users = [(4, channel.called_user.id)]
        if channel.called_pbx_user and channel.called_pbx_user not in channel.call.called_pbx_users:
            channel.call.called_pbx_users = [(4, channel.called_pbx_user.id)]
        # Check if we need to set a partner from child channel
        if not channel.call.partner and channel.partner:
            channel.call.partner = channel.partner
        if (channel.call.direction == 'incoming' and params.get('CallStatus') == 'initiated' and
                params.get('To').startswith('sip:')):
            # Desktop notification only for SIP calls.
            channel.connect_notify()
        # Register call when ALL channels have ended (not just the latest one).
        # This prevents false "no-answer" notifications when one dial attempt fails
        # but another is still active.
        if params.get('CallStatus') in CALL_END_STATUSES:
            # Check if all channels have ended
            all_channels_ended = all(
                ch.status in CALL_END_STATUSES
                for ch in channel.call.channels
            )
            if all_channels_ended:
                self.register_call(channel, params)
            # Fetch call price if enabled in settings
            if self.env['connect.settings'].sudo().get_param('fetch_call_prices'):
                self.save_call_price(channel.call, params)
        # Reload call view
        self.env['connect.settings'].connect_reload_view('connect.call')
        if params.get('ErrorCode') and params.get('ErrorCode') not in IGNORE_ERROR_CODES:
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
        return channel.call.id

    @api.model
    def on_vm_recording_status(self, params):
        debug(self.sudo(), 'On recording status: %s' % json.dumps(params, indent=2))
        channel = self.sudo().env['connect.channel'].search([('sid', '=', params['CallSid'])])
        if channel and channel.call:
            updates = {
                'voicemail_url': params.get('RecordingUrl'),
                'voicemail_duration': int(params.get('RecordingDuration'))
            }
            # Voicemail was left - update status to 'voicemail'
            # Only if not already 'answered' (answered takes priority over voicemail)
            if channel.call.status != 'answered':
                updates['status'] = 'voicemail'
            channel.call.write(updates)
        return True

    @api.model
    def on_call_action(self, params):
        debug(self, 'On call action: %s' % params)
        return '<Response><Hangup/></Response>'

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

    def register_call(self, channel, params):
        try:
            notify_users = []
            # Construct message from lines
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
                # Missed call notification, filter users who have it enabled.
                for user in channel.call.called_users:
                    if user.connect_user[0].missed_calls_notify:
                        notify_users.append(user)
            # Register call at partner.
            if channel.call.partner:
                message.insert(3, 'partner: {}, '.format(channel.call.partner.name))
                final_message = ' '.join(message)
                if final_message.endswith(', '):
                    final_message = final_message[:-2] + '.'
                channel.call.register_call_post_message(
                    channel.call.partner, body=final_message, subtype_xmlid='mail.mt_note')
            # Register call to users - send missed call notification if not handled
            # 'answered' and 'voicemail' are considered "handled" - no missed notification
            handled_statuses = ['answered', 'voicemail', 'completed']  # 'completed' for backwards compat
            if channel.call.direction == 'incoming' and channel.call.status not in handled_statuses and notify_users:
                debug(self, 'Missed call notification to users: {}'.format(notify_users))
                final_message = ' '.join(message)
                if final_message.endswith(', '):
                    final_message = final_message[:-2] + '.'
                channel.call.register_call_post_message(
                    channel.call,
                    subtype_xmlid='mail.mt_comment',
                    subject=channel.call.name,
                    body=final_message,
                    partner_ids=[k.partner_id.id for k in notify_users]
                )
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

    def transfer(self, user=None):
        self.ensure_one()
        if False:  # self.status not in ['in-progress', 'ringing']:
            logger.warning('Call not in progress, cannot transfer')
            return
        # Get the PBX user doing trasnfer
        if not user:
            user = self.env.user.connect_user
            user = self.channels[0].caller_pbx_user or self.channels[0].called_pbx_user
        """
        # Case 1: User is on primary channel.
        primary_channel = self.channels.filtered(lambda x: x.parent_channel == False)
        if primary_channel and primary_channel.caller_pbx_user:
            print(111, 'PRIMARY CHANNEL CALLER', primary_channel)
        elif primary_channel and primary_channel.called_pbx_user:
            print(1111, 'PRIMARY CHANNEL CALLED', primary_channel)
        # Find current user on all channels.
        print(111111, self.channels)
        """
        user_channel = self.channels.filtered(
            lambda x: (x.caller_pbx_user == user or x.called_pbx_user == user))
        if not user_channel:
            logger.warning('Cannot get user channel for call %s for user %s', self.id, user.name)
            return
        other_channel = self.channels - user_channel
        if len(other_channel) != 1:
            logger.warning('Cannot transfer call, number of other channels: %s', len(other_channel))
            return
        client = self.env['connect.settings'].get_client()
        conf_id = uuid.uuid4().hex

        def transfer_other():
            # Put other channel into conference.
            response = VoiceResponse()
            self.tts_system_message(response, 'system.transfer')
            dial = Dial()
            dial.conference('user-{}-{}'.format(user.id, conf_id))
            response.append(dial)
            # response.play('http://com.twilio.music.classical.s3.amazonaws.com/BusyStrings.mp3')
            client.calls(other_channel.sid).update(twiml=response)

        def transfer_user():
            # Dial a new call party.
            response = VoiceResponse()
            self.tts_system_message(response, 'system.transfer')
            dial = Dial()
            sip = Sip('sip:user@devmax17.sip.twilio.com')
            # dial.conference('user-{}-{}'.format(user.id,  conf_id))
            dial.append(sip)
            response.append(dial)
            # response.play('http://com.twilio.music.classical.s3.amazonaws.com/BusyStrings.mp3')
            client.calls(user_channel.sid).update(twiml=response)

        transfer_user()
        transfer_other()

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

        try:
            # Step 1: Put the other party into a conference (with hold music)
            response_other = VoiceResponse()
            self.tts_system_message(response_other, 'system.transfer')
            dial_conf = Dial()
            dial_conf.conference(
                conf_name,
                startConferenceOnEnter=True,
                endConferenceOnExit=True,
                waitUrl='http://twimlets.com/holdmusic?Bucket=com.twilio.music.classical'
            )
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
            "direction"
        ]
