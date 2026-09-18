# -*- coding: utf-8 -*-
"""Audio-layer wiring for callflows.

connect.callflow owns the Twilio routing shape; the prompts it plays, the
voicemail box it hands calls to, and its participation in the audio
reachability graph are properties of the audio layer and are declared here.
"""

from odoo import fields, models

from .audio_referrer_mixin import SELECTABLE_AUDIO_STATES


class CallflowChoice(models.Model):
    _name = 'connect.callflow_choice'
    _inherit = ['connect.callflow_choice', 'connect.audio.referrer.mixin']

    _audio_reachability_fields = ('exten', 'callflow')


class CallFlow(models.Model):
    _name = 'connect.callflow'
    _inherit = ['connect.callflow', 'connect.tts.mixin',
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
