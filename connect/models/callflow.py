# -*- coding: utf-8 -*-

import logging
from urllib.parse import urljoin
from odoo import fields, models, api, release
from twilio.twiml.voice_response import Gather, VoiceResponse, Say, Client, Sip, Dial
from .audio_referrer_mixin import SELECTABLE_AUDIO_STATES
from .twiml import pretty_xml
from .settings import debug

logger = logging.getLogger(__name__)

class CallflowChoice(models.Model):
    _name = 'connect.callflow_choice'
    _inherit = ['connect.audio.referrer.mixin']
    _description = 'Callflow Choice'

    _audio_reachability_fields = ('exten', 'callflow')

    callflow = fields.Many2one('connect.callflow', required=True, ondelete='cascade')
    choice_digits = fields.Char(required=True)
    exten = fields.Many2one('connect.exten', ondelete='restrict', required=True)
    speech = fields.Char()


class CallFlow(models.Model):
    _name = 'connect.callflow'
    _inherit = ['connect.tts.mixin', 'connect.audio.referrer.mixin']
    _description = 'Call Flow'
    _order = 'name asc'

    _audio_reference_fields = (
        'prompt_audio_id', 'invalid_input_audio_id', 'voicemail_audio_id',
        'after_hours_audio_id',
    )
    _audio_reference_trigger_fields = ('active',)
    _audio_reachability_fields = (
        'active', 'ring_users', 'voicemail_enabled', 'schedule_id', 'choices',
        'prompt_audio_id', 'invalid_input_audio_id', 'voicemail_audio_id',
        'after_hours_audio_id', 'business_hours_enabled',
    )

    name = fields.Char(required=True)
    exten = fields.Many2one('connect.exten', ondelete='set null', readonly=True)
    exten_number = fields.Char(related='exten.number', store=True)
    language = fields.Char(default='en-US', required=True)
    gather_input = fields.Boolean()
    gather_input_type = fields.Selection(string='Input Type',
        selection=[
            ('dtmf speech', 'DTMF + speech'),
            ('dtmf', 'DTMF'),
            ('speech', 'Speech')
        ], required=True, default='dtmf speech')
    gather_timeout = fields.Integer(string='Timeout', default=5)
    gather_hints = fields.Char('Hints', default='This is a phrase I expect to hear, department name or extension number')
    prompt_audio_id = fields.Many2one('connect.audio', ondelete='set null',
        domain=[('state', 'in', SELECTABLE_AUDIO_STATES)],
        string='Prompt Audio',
        help='Audio played when the callflow opens.')
    prompt_preview = fields.Html(
        related='prompt_audio_id.latest_utterance_id.preview_audio',
        string='Prompt Preview', sanitize=False)
    invalid_input_audio_id = fields.Many2one('connect.audio', ondelete='set null',
        domain=[('state', 'in', SELECTABLE_AUDIO_STATES)],
        string='Invalid Input Audio',
        help='Audio played when the caller\'s DTMF/speech input does not match '
             'any configured choice.')
    invalid_input_preview = fields.Html(
        related='invalid_input_audio_id.latest_utterance_id.preview_audio',
        string='Invalid Input Preview', sanitize=False)
    gather_digits = fields.Integer(required=True, default=1)
    choices = fields.One2many('connect.callflow_choice', 'callflow')
    gather_action_url = fields.Char(compute='_get_gather_action_url')
    ring_users = fields.Many2many('connect.user')
    record_calls = fields.Boolean()
    voicemail_audio_id = fields.Many2one('connect.audio', ondelete='set null',
        domain=[('state', 'in', SELECTABLE_AUDIO_STATES)],
        string='Voicemail Prompt Audio',
        help='Audio played before voicemail recording on this callflow.')
    voicemail_preview = fields.Html(
        related='voicemail_audio_id.latest_utterance_id.preview_audio',
        string='Voicemail Preview', sanitize=False)
    voicemail_enabled = fields.Boolean()
    voicemail_box_id = fields.Many2one(
        'connect.voicemail_box', ondelete='set null', string='Voicemail Box',
        help='Shared box for voicemails landing on this callflow. All box members gain access to its calls and voicemails.')
    # fallback_extension
    schedule_id = fields.Many2one(
        'connect.schedule', string='Schedule',
        help="Business hours schedule. If set, overrides the simple business hours fields below.")
    business_hours_enabled = fields.Boolean(
        string='Enable Business Hours', default=False,
        help='Route calls differently outside business hours')
    business_hours_start = fields.Float(
        string='Business Hours Start', default=9.0,
        help='Start of business hours (24h format, e.g., 9.0 = 9:00 AM)')
    business_hours_end = fields.Float(
        string='Business Hours End', default=17.0,
        help='End of business hours (24h format, e.g., 17.0 = 5:00 PM)')
    business_hours_timezone = fields.Selection(
        '_tz_get', string='Timezone', default='US/Eastern',
        help='Timezone for business hours calculation')
    after_hours_audio_id = fields.Many2one('connect.audio', ondelete='set null',
        domain=[('state', 'in', SELECTABLE_AUDIO_STATES)],
        string='After Hours Audio',
        help='Audio played to callers outside of configured business hours.')
    after_hours_preview = fields.Html(
        related='after_hours_audio_id.latest_utterance_id.preview_audio',
        string='After Hours Preview', sanitize=False)
    after_hours_voicemail = fields.Boolean(
        string='After Hours Voicemail', default=True,
        help='Allow voicemail after hours message')
    active = fields.Boolean(default=True,
        help='Archived callflows are excluded from routing and appear with '
             'inactive_reason="callflow archived" in the audio Where-Used tab.')

    def create_extension(self):
        self.ensure_one()
        return self.env['connect.exten'].create_extension(self, 'callflow')

    @api.model
    def _tz_get(self):
        import pytz
        return [(tz, tz) for tz in sorted(pytz.all_timezones_set)]

    def _is_business_hours(self):
        """Check if current time is within configured business hours.

        If a schedule is set, delegates to it. Otherwise falls back to the
        simple start/end hour fields.
        """
        self.ensure_one()

        # Advanced schedule takes precedence
        if self.schedule_id:
            return self.schedule_id.is_open()

        if not self.business_hours_enabled:
            return True  # No hours configured = always open

        import pytz
        from datetime import datetime

        tz = pytz.timezone(self.business_hours_timezone or 'UTC')
        now = datetime.now(tz)
        current_hour = now.hour + now.minute / 60.0

        # Handle overnight hours (e.g., 22:00 - 06:00)
        if self.business_hours_start <= self.business_hours_end:
            return self.business_hours_start <= current_hour < self.business_hours_end
        else:
            return current_hour >= self.business_hours_start or current_hour < self.business_hours_end

    def _render_after_hours(self):
        """Render TwiML for after-hours calls."""
        self.ensure_one()
        response = VoiceResponse()

        if self.after_hours_audio_id:
            try:
                self.sudo().after_hours_audio_id.play_on(response, record=self)
            except Exception as e:
                logger.error('After-hours audio render failed for callflow %s: %s', self.id, e)
                self._say_fallback(response,
                    'Thank you for calling. Our office is currently closed.')

        if self.after_hours_voicemail:
            self._say_fallback(response, 'Please leave a message after the tone.')
            vm_max_length = self.env['connect.settings'].sudo().get_param('voicemail_max_length') or 120
            vm_finish_key = self.env['connect.settings'].sudo().get_param('voicemail_finish_key') or '#'
            response.record(maxLength=vm_max_length, finishOnKey=vm_finish_key, playBeep=True)
        else:
            response.hangup()

        debug(self, pretty_xml(str(response)))
        return response

    def _get_gather_action_url(self):
        api_url = self.env['connect.settings'].get_param('api_url')
        edge = self.env['connect.settings'].get_param('twilio_edge')
        for rec in self:
            rec.gather_action_url = urljoin(api_url,
                'twilio/webhook/callflow/{}/gather#e={}'.format(rec.id, edge))

    @api.model
    def gather_action(self, flow_id, request):
        callflow = self.browse(flow_id)
        choice = callflow.choices.filtered(
            lambda x: x.choice_digits == request.get('Digits') or
                (x.speech and request.get('SpeechResult') and x.speech in
                request.get('SpeechResult', '')))
        if not choice:
            logger.warning('Gather choice digits: %s, speech: %s not found in Call Flow %s',
                request.get('Digits'), request.get('SpeechResult'), callflow.name)
            return callflow.render(request=request, params={'invalid_input': True})
        return choice[0].exten.render(request=request)

    def render(self, request={}, params={}):
        self.ensure_one()
        # Check business hours
        if not self._is_business_hours():
            return self._render_after_hours()
        api_url = self.env['connect.settings'].sudo().get_param('api_url')
        edge = self.env['connect.settings'].sudo().get_param('twilio_edge')
        voicemail_record_status_url = urljoin(api_url,
                                            'twilio/webhook/vm_recordingstatus#e={}'.format(edge))
        status_url = urljoin(api_url, 'twilio/webhook/callstatus#e={}'.format(edge))
        action_url = urljoin(api_url, 'twilio/webhook/connect.callflow/call_action/{}#e={}'.format(self.id, edge))
        record_status_url = urljoin(api_url, 'twilio/webhook/recordingstatus#e={}'.format(edge))
        invalid_input = params.get('invalid_input')
        response = VoiceResponse()
        if invalid_input:
            self.get_gather_invalid_input_message(response)
        # gather_input=True always gets a prompt: get_prompt_message emits a
        # generic <Say> fallback when prompt_audio_id is unset so the Gather
        # window never opens on silence.
        # gather_input=False only plays a prompt when one is explicitly set —
        # a plain ringall callflow should ring users without a confusing
        # "Please make a selection." intro.
        if self.gather_input:
            gather = Gather(
                action=self.gather_action_url,
                method='POST',
                timeout=self.gather_timeout,
                numDigits=str(self.gather_digits),
                input=self.gather_input_type,
                language=self.language
            )
            self.get_prompt_message(gather)
            response.append(gather)
        elif self.prompt_audio_id:
            self.get_prompt_message(response)
        # Add ringall users
        if self.ring_users:
            callerId = request.get('Caller')
            # Hack to enable testing callflow from SIP or Client.
            if callerId.startswith('sip:') or callerId.startswith('client:'):
                # Take the default number
                callerId = self.env['connect.outgoing_callerid'].sudo().search(
                    [('is_default', '=', True)], limit=1).number
                if not callerId:
                    response = VoiceResponse()
                    self.tts_system_message(response, 'error.no_callerid')
                    return response
            if self.record_calls:
                dial = Dial(callerId=callerId, action=action_url,
                        record='record-from-answer-dual', recordingStatusCallback=record_status_url)
            else:
                dial = Dial(callerId=callerId, action=action_url)
            for user in self.ring_users:
                callflows = self.env['connect.user_callflow'].sudo().search(
                    [('callflow_type', 'in', ['sip', 'client']), ('user', '=', user.id)], order='prio')
                for callflow in callflows:
                    if callflow.callflow_type == 'sip':
                        dial.sip('sip:{}'.format(user.uri),
                                statusCallbackEvent='answered completed',
                                statusCallback=status_url)
                    else:
                        client = Client(
                            statusCallbackEvent='answered completed',
                            statusCallback=status_url)
                        client.identity(user.get_client_identity())
                        client.parameter(name='CallerName', value=callerId)
                        dial.append(client)
            response.append(dial)
        else:
            # No ring users set, just send to voicemail if enabled.
            if self.voicemail_enabled:
                self._stamp_voicemail_box(request)
                response.pause(length=1)
                # get_voicemail_prompt_message falls back to a generic <Say>
                # when voicemail_audio_id is unset so the caller is never
                # dropped into a silent <Record>.
                self.get_voicemail_prompt_message(response)
                vm_max_length = self.env['connect.settings'].sudo().get_param('voicemail_max_length') or 120
                vm_finish_key = self.env['connect.settings'].sudo().get_param('voicemail_finish_key') or '#'
                response.record(
                    maxLength=vm_max_length,
                    finishOnKey=vm_finish_key,
                    playBeep=True,
                    recordingStatusCallback=voicemail_record_status_url)
            else:
                # No voicemail, just say sorry and hangup.
                self.tts_system_message(response, 'error.callflow_empty')
                response.pause(length=1)
                response.hangup()
        debug(self, pretty_xml(str(response)))
        return response

    def _say_fallback(self, response, text):
        """Emit a <Say> with the DB default Twilio voice and pronunciation
        rules applied. Used when an audio m2o is missing or play_on raises
        so the caller always hears something sensible instead of silence."""
        Settings = self.env['connect.settings'].sudo()
        voice = Settings.get_system_voice()
        processed = Settings.process_pronunciation(text)
        response.say(processed, voice=voice, language=self.language)

    def get_prompt_message(self, response):
        """Play prompt_audio_id. Silent no-op only when both audio AND a
        reasonable fallback text are absent — by default we never leave the
        caller inside a silent <Gather>."""
        debug(self, 'Saying prompt message for Call Flow {}'.format(self.name))
        if self.prompt_audio_id:
            try:
                self.sudo().prompt_audio_id.play_on(response, record=self)
                return
            except Exception as e:
                logger.error('Prompt audio render failed for callflow %s: %s', self.id, e)
        # Either the m2o is unset or playback failed. A generic <Say> prevents
        # a silent DTMF/speech gather window.
        self._say_fallback(response, 'Please make a selection.')

    def get_gather_invalid_input_message(self, response):
        """Play invalid_input_audio_id or fall back to a generic retry prompt."""
        if self.invalid_input_audio_id:
            try:
                self.sudo().invalid_input_audio_id.play_on(response, record=self)
                return
            except Exception as e:
                logger.error('Invalid input audio render failed for callflow %s: %s', self.id, e)
        self._say_fallback(response, 'We received wrong input. Please try again.')

    def _stamp_voicemail_box(self, request):
        """Tag the active call with this callflow's voicemail box so members of
        the box gain access to the call record and the resulting voicemail."""
        self.ensure_one()
        if not self.voicemail_box_id:
            return
        call_sid = (request or {}).get('CallSid')
        if not call_sid:
            return
        channel = self.env['connect.channel'].sudo().search([('sid', '=', call_sid)], limit=1)
        if channel.call and not channel.call.voicemail_box_id:
            channel.call.sudo().voicemail_box_id = self.voicemail_box_id.id

    def get_voicemail_prompt_message(self, response):
        """Play voicemail_audio_id or fall back to a generic voicemail prompt
        so <Record> is never preceded by silence."""
        if self.voicemail_audio_id:
            try:
                self.sudo().voicemail_audio_id.play_on(response, record=self)
                return
            except Exception as e:
                logger.error('Voicemail audio render failed for callflow %s: %s', self.id, e)
        self._say_fallback(response, 'Please leave a message after the tone.')

    @api.model
    def on_call_action(self, flow_id, request):
        response = VoiceResponse()
        if request.get('DialCallStatus') != 'completed':
            callflow = self.browse(flow_id)
            # The call was not connected, point to voicemail when enabled.
            # Guard on voicemail_enabled (operator intent) rather than the
            # m2o — get_voicemail_prompt_message handles the missing-audio
            # case with a generic <Say> fallback.
            if callflow.voicemail_enabled:
                callflow._stamp_voicemail_box(request)
                api_url = self.env['connect.settings'].sudo().get_param('api_url')
                edge = self.env['connect.settings'].sudo().get_param('twilio_edge')
                record_status_url = urljoin(api_url, 'twilio/webhook/vm_recordingstatus#e={}'.format(edge))
                response.pause(length=1)
                callflow.get_voicemail_prompt_message(response)
                vm_max_length = self.env['connect.settings'].sudo().get_param('voicemail_max_length') or 120
                vm_finish_key = self.env['connect.settings'].sudo().get_param('voicemail_finish_key') or '#'
                response.record(
                    maxLength=vm_max_length,
                    finishOnKey=vm_finish_key,
                    playBeep=True,
                    recordingStatusCallback=record_status_url)
            else:
                # No voicemail, just say sorry and hangup.
                self.tts_system_message(response, 'error.call_failed')
                response.pause(length=1)
                response.hangup()
        else:
            # Call was connected, just hangup if the call was hangup by
            # the called party and the caller is still here.
            response.hangup()
        debug(self, pretty_xml(str(response)))
        return response
