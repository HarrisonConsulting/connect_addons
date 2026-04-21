# -*- coding: utf-8 -*-

import logging

from odoo import models, fields, api

logger = logging.getLogger(__name__)

# (text field, audio m2o, root_var). root_var is None because callflow text
# fields don't use Jinja today; setting model_id still allows operators to add
# {field} tokens manually.
CALLFLOW_AUDIO_FIELDS = (
    ('prompt_message', 'prompt_audio_id', None),
    ('invalid_input_message', 'invalid_input_audio_id', None),
    ('voicemail_prompt', 'voicemail_audio_id', None),
)


class ElevenLabsCallflow(models.Model):
    _name = 'connect.callflow'
    _inherit = ['connect.callflow', 'connect.audio.referrer.mixin']

    _audio_reference_fields = (
        'prompt_audio_id', 'invalid_input_audio_id', 'voicemail_audio_id',
    )
    _audio_reference_trigger_fields = ('active',)
    # Superset of the base callflow's reachability fields plus our audio m2os.
    # Duplicated explicitly because Python's class-attribute inheritance would
    # otherwise have ElevenLabs shadow rather than extend the base tuple.
    _audio_reachability_fields = (
        'active', 'ring_users', 'voicemail_enabled', 'schedule_id', 'choices',
        'prompt_audio_id', 'invalid_input_audio_id', 'voicemail_audio_id',
    )

    elevenlabs_enabled = fields.Boolean(compute='_get_elevenlabs_enabled')
    prompt_audio_id = fields.Many2one('connect.audio', ondelete='set null',
        string='Prompt Audio')
    invalid_input_audio_id = fields.Many2one('connect.audio', ondelete='set null',
        string='Invalid Input Audio')
    voicemail_audio_id = fields.Many2one('connect.audio', ondelete='set null',
        string='Voicemail Prompt Audio')
    prompt_message_widget = fields.Html(
        related='prompt_audio_id.latest_utterance_id.preview_audio',
        string='Prompt Preview')
    invalid_input_message_widget = fields.Html(
        related='invalid_input_audio_id.latest_utterance_id.preview_audio',
        string='Invalid Input Preview')
    voicemail_prompt_widget = fields.Html(
        related='voicemail_audio_id.latest_utterance_id.preview_audio',
        string='Voicemail Preview')

    def _get_elevenlabs_enabled(self):
        elevenlabs_enabled = self.env['connect.settings'].sudo().get_param('elevenlabs_enabled')
        for rec in self:
            rec.elevenlabs_enabled = elevenlabs_enabled

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            rec._sync_audio_fields()
        return records

    def write(self, vals):
        watched = {f for f, _, _ in CALLFLOW_AUDIO_FIELDS} | {'voicemail_enabled'}
        result = super().write(vals)
        if watched & vals.keys():
            self._sync_audio_fields()
        return result

    def _sync_audio_fields(self):
        for rec in self:
            for text_field, audio_field, root_var in CALLFLOW_AUDIO_FIELDS:
                rec._sync_one_audio(text_field, audio_field, root_var)

    def _sync_one_audio(self, text_field, audio_field, root_var):
        self.ensure_one()
        Audio = self.env['connect.audio'].sudo()
        text = self[text_field]
        audio = self[audio_field]

        if text_field == 'voicemail_prompt' and not self.voicemail_enabled:
            text = False

        auto_name = f'{self.name} {text_field}'

        # Operator manually selected a library audio — do not overwrite it.
        if audio and audio.name != auto_name:
            return

        if not text:
            if audio:
                audio.unlink()
            return

        source, voice = self._resolve_audio_source_voice()
        is_dynamic, static_text, model_id = self._template_args(text, root_var)
        vals = {
            'name': auto_name,
            'source': source,
            'voice_id': voice.id if voice else False,
            'static_text': static_text,
            'is_dynamic': is_dynamic,
            'model_id': model_id,
        }
        if audio:
            audio.write(vals)
        else:
            audio = Audio.create(vals)
            self[audio_field] = audio
        try:
            audio.with_delay()._precache(record=self)
        except Exception as e:
            logger.debug('queue_job pre-cache skipped: %s', e)

    def _resolve_audio_source_voice(self):
        return self.env['connect.settings'].sudo().get_default_audio_source()

    def _template_args(self, text, root_var):
        Audio = self.env['connect.audio'].sudo()
        if not root_var:
            return False, text, False
        converted, ok = Audio.jinja_to_token(text, root_var=root_var)
        if ok and converted != text:
            model = self.env['ir.model'].sudo().search([('model', '=', self._name)], limit=1)
            return True, converted, model.id
        return False, text, False

    def get_prompt_message(self, gather):
        if self.sudo().prompt_audio_id:
            try:
                self.sudo().prompt_audio_id.play_on(gather, record=self)
            except Exception as e:
                logger.error('Audio render failed for callflow %s prompt: %s', self.id, e)

    def get_gather_invalid_input_message(self, response):
        if self.sudo().invalid_input_audio_id:
            try:
                self.sudo().invalid_input_audio_id.play_on(response, record=self)
            except Exception as e:
                logger.error('Audio render failed for callflow %s invalid input: %s', self.id, e)

    def get_voicemail_prompt_message(self, response):
        if self.sudo().voicemail_audio_id:
            try:
                self.sudo().voicemail_audio_id.play_on(response, record=self)
            except Exception as e:
                logger.error('Audio render failed for callflow %s voicemail: %s', self.id, e)
