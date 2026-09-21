# -*- coding: utf-8 -*-
"""Audio-layer wiring for Twilio callflows.

connect.twilio.callflow owns the Twilio routing shape; the prompts it plays,
the voicemail box it hands calls to, business-hours routing, and its
participation in the audio reachability graph are declared here.
"""

import logging
from urllib.parse import urljoin

from odoo import api, fields, models
from twilio.twiml.voice_response import VoiceResponse

from odoo.addons.connect.models.settings import debug
from odoo.addons.connect_twilio.models.twiml import pretty_xml

from .audio_referrer_mixin import SELECTABLE_AUDIO_STATES

logger = logging.getLogger(__name__)


class CallflowChoice(models.Model):
    _name = 'connect.twilio.callflow_choice'
    _inherit = ['connect.twilio.callflow_choice', 'connect.audio.referrer.mixin']

    _audio_reachability_fields = ('exten', 'callflow')


class CallFlow(models.Model):
    _name = 'connect.twilio.callflow'
    _inherit = ['connect.twilio.callflow', 'connect.tts.mixin',
                'connect.audio.referrer.mixin']

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
    voicemail_audio_id = fields.Many2one('connect.audio', ondelete='set null',
        domain=[('state', 'in', SELECTABLE_AUDIO_STATES)],
        string='Voicemail Prompt Audio',
        help='Audio played before voicemail recording on this callflow.')
    voicemail_preview = fields.Html(
        related='voicemail_audio_id.latest_utterance_id.preview_audio',
        string='Voicemail Preview', sanitize=False)
    voicemail_box_id = fields.Many2one(
        'connect.voicemail_box', ondelete='set null', string='Voicemail Box',
        help='Shared box for voicemails landing on this callflow. All box members gain access to its calls and voicemails.')
    after_hours_audio_id = fields.Many2one('connect.audio', ondelete='set null',
        domain=[('state', 'in', SELECTABLE_AUDIO_STATES)],
        string='After Hours Audio',
        help='Audio played to callers outside of configured business hours.')
    after_hours_preview = fields.Html(
        related='after_hours_audio_id.latest_utterance_id.preview_audio',
        string='After Hours Preview', sanitize=False)
    schedule_id = fields.Many2one(
        'connect.schedule', string='Schedule',
        help='Business hours schedule. If set, overrides the simple business hours fields below.')
    business_hours_enabled = fields.Boolean(
        string='Enable Business Hours', default=False,
        help='Route calls differently outside business hours.')
    business_hours_start = fields.Float(
        string='Business Hours Start', default=9.0,
        help='Start of business hours (24h format, e.g., 9.0 = 9:00 AM).')
    business_hours_end = fields.Float(
        string='Business Hours End', default=17.0,
        help='End of business hours (24h format, e.g., 17.0 = 5:00 PM).')
    business_hours_timezone = fields.Selection(
        '_tz_get', string='Timezone', default='US/Eastern',
        help='Timezone for business hours calculation.')
    after_hours_voicemail = fields.Boolean(
        string='After Hours Voicemail', default=True,
        help='Allow voicemail after the after-hours message.')
    active = fields.Boolean(
        default=True,
        help='Archived callflows are excluded from routing and appear with '
             'inactive_reason="callflow archived" in the audio Where-Used tab.')

    @api.model
    def _tz_get(self):
        import pytz
        return [(tz, tz) for tz in sorted(pytz.all_timezones_set)]

    def _is_business_hours(self):
        self.ensure_one()
        if self.schedule_id:
            return self.schedule_id.is_open()
        if not self.business_hours_enabled:
            return True
        import pytz
        from datetime import datetime
        tz = pytz.timezone(self.business_hours_timezone or 'UTC')
        now = datetime.now(tz)
        current_hour = now.hour + now.minute / 60.0
        if self.business_hours_start <= self.business_hours_end:
            return self.business_hours_start <= current_hour < self.business_hours_end
        return current_hour >= self.business_hours_start or current_hour < self.business_hours_end

    def _say_fallback(self, response, text):
        Settings = self.env['connect.settings'].sudo()
        voice = Settings.get_system_voice()
        processed = Settings.process_pronunciation(text)
        response.say(processed, voice=voice, language=self.language)

    def _render_after_hours(self):
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
            api_url = self.env['connect.settings'].sudo().get_param('api_url')
            edge = self.env['connect.settings'].sudo().get_param('twilio_edge')
            response.record(
                maxLength=vm_max_length,
                finishOnKey=vm_finish_key,
                playBeep=True,
                action=urljoin(api_url, 'twilio/webhook/{}/call_action/{}#e={}'.format(
                    self._name, self.id, edge)),
                recordingStatusCallback=urljoin(
                    api_url, 'twilio/webhook/vm_recordingstatus#e={}'.format(edge)))
        else:
            response.hangup()
        debug(self, pretty_xml(str(response)))
        return response

    def render(self, request={}, params={}):
        self.ensure_one()
        if not self._is_business_hours():
            return self._render_after_hours()
        return super().render(request=request, params=params)

    def get_prompt_message(self, response):
        debug(self, 'Saying prompt message for Call Flow {}'.format(self.name))
        if self.prompt_audio_id:
            try:
                self.sudo().prompt_audio_id.play_on(response, record=self)
                return
            except Exception as e:
                logger.error('Prompt audio render failed for callflow %s: %s', self.id, e)
        self._say_fallback(response, 'Please make a selection.')

    def get_gather_invalid_input_message(self, response):
        if self.invalid_input_audio_id:
            try:
                self.sudo().invalid_input_audio_id.play_on(response, record=self)
                return
            except Exception as e:
                logger.error('Invalid input audio render failed for callflow %s: %s', self.id, e)
        self._say_fallback(response, 'We received wrong input. Please try again.')

    def get_voicemail_prompt_message(self, response):
        if self.voicemail_audio_id:
            try:
                self.sudo().voicemail_audio_id.play_on(response, record=self)
                return
            except Exception as e:
                logger.error('Voicemail audio render failed for callflow %s: %s', self.id, e)
        self._say_fallback(response, 'Please leave a message after the tone.')
