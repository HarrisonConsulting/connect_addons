# -*- coding: utf-8 -*-

import logging
from odoo import models, fields, release, api

logger = logging.getLogger(__name__)


class ElevenLabsUser(models.Model):
    _inherit = 'connect.user'

    elevenlabs_enabled = fields.Boolean(compute='_get_elevenlabs_enabled')
    greeting_message_file = fields.Many2one('connect.elevenlabs_file', ondelete='set null')
    voicemail_prompt_file = fields.Many2one('connect.elevenlabs_file', ondelete='set null')
    if release.version_info[0] >= 17.0:
        greeting_message_widget = fields.Html(related='greeting_message_file.preview_audio')
        voicemail_prompt_widget = fields.Html(related='voicemail_prompt_file.preview_audio')
    else:
        greeting_message_widget = fields.Char(related='greeting_message_file.preview_audio')
        voicemail_prompt_widget = fields.Char(related='voicemail_prompt_file.preview_audio')

    def _get_elevenlabs_enabled(self):
        elevenlabs_enabled = self.env['connect.settings'].sudo().get_param('elevenlabs_enabled')
        for rec in self:
            rec.elevenlabs_enabled = elevenlabs_enabled

    @api.constrains('greeting_message')
    def _generate_elevenlabs_greeting_message(self):
        elevenlabs_enabled = self.env['connect.settings'].sudo().get_param('elevenlabs_enabled')
        for rec in self:
            if elevenlabs_enabled and rec.greeting_message:
                if rec.greeting_message_file:
                    rec.greeting_message_file.text = rec.greeting_message
                else:
                    rec.greeting_message_file = self.env['connect.elevenlabs_file'].create({'text': rec.greeting_message})

    @api.constrains('voicemail_prompt', 'voicemail_enabled')
    def _generate_elevenlabs_voicemail_prompt(self):
        elevenlabs_enabled = self.env['connect.settings'].sudo().get_param('elevenlabs_enabled')
        for rec in self:
            if elevenlabs_enabled and rec.voicemail_enabled:
                voicemail_prompt = rec.render_voicemail_prompt()
                if rec.voicemail_prompt_file:
                    rec.voicemail_prompt_file.text = voicemail_prompt
                else:
                    rec.voicemail_prompt_file = self.env['connect.elevenlabs_file'].create({'text': voicemail_prompt})

    def get_greeting_message(self, response):
        try:
            self = self.sudo()
            if not self.env['connect.settings'].sudo().get_param('elevenlabs_enabled'):
                return super().get_greeting_message(response)
            if not self.greeting_message_file or not self.greeting_message_file.file:
                self._generate_elevenlabs_greeting_message()
            if self.greeting_message_file and self.greeting_message_file.file:
                response.play(self.greeting_message_file.get_file_url())
                return
        except Exception as e:
            logger.error('Elevenlabs error: %s', e)
        return super().get_greeting_message(response)

    def get_voicemail_prompt(self, response):
        try:
            self = self.sudo()
            if not self.env['connect.settings'].sudo().get_param('elevenlabs_enabled'):
                return super().get_voicemail_prompt(response)
            if not self.voicemail_prompt_file or not self.voicemail_prompt_file.file:
                self._generate_elevenlabs_voicemail_prompt()
            if self.voicemail_prompt_file and self.voicemail_prompt_file.file:
                response.play(self.voicemail_prompt_file.get_file_url())
                return
        except Exception as e:
            logger.error('Elevenlabs error: %s', e)
        return super().get_voicemail_prompt(response)
