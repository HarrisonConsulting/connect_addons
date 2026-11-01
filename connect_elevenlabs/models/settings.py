# -*- coding: utf-8 -*-

import logging
import urllib.parse
from urllib.parse import urljoin
import requests
import uuid
from elevenlabs import ElevenLabs

from odoo import fields, models
from odoo.addons.connect.models.settings import PROTECTED_FIELDS, HTTP_API_TIMEOUT
from odoo.exceptions import ValidationError

logger = logging.getLogger(__name__)

PROTECTED_FIELDS.append('display_elevenlabs_api_key')
PROTECTED_FIELDS.append('display_elevenlabs_post_call_webhook_secret')

class Elevenlabsettings(models.Model):
    _inherit = 'connect.settings'

    elevenlabs_api_key = fields.Char(groups="base.group_erp_manager")
    elevenlabs_agent_token = fields.Char(required=True, groups="base.group_erp_manager",
                                        default=lambda x: str(uuid.uuid4()))
    display_elevenlabs_api_key = fields.Char()
    elevenlabs_voice = fields.Many2one('connect.voice', ondelete='set null', string='Selected Voice',
        domain=[('provider', '=', 'elevenlabs')])
    elevenlabs_enabled = fields.Boolean()
    elevenlabs_model_id = fields.Char(string='Model', default='eleven_flash_v2_5',
        help='ElevenLabs TTS model. flash_v2_5 = fastest / IVR-appropriate, '
             'turbo_v2_5 = balanced, multilingual_v2 = highest quality but slower. '
             'Changing this invalidates cached utterances.')
    elevenlabs_stability = fields.Float(string='Stability', default=0.5,
        help='0.0 = maximum expression, 1.0 = maximum consistency. '
             '~0.5 is a typical IVR balance.')
    elevenlabs_similarity_boost = fields.Float(string='Similarity Boost', default=0.75,
        help='Adherence to the reference voice. Higher = closer but may amplify artifacts.')
    elevenlabs_style = fields.Float(string='Style', default=0.0,
        help='Style exaggeration. 0.0 is recommended for IVR; higher increases latency.')
    elevenlabs_speaker_boost = fields.Boolean(string='Speaker Boost', default=True,
        help='Boost similarity to the original speaker at a small compute cost.')
    elevenlabs_agent_url = fields.Char(string='Agent URL', required=True, default='https://elevenlabs-agent.ngrok.io')
    elevenlabs_agent_parameters = fields.Text(string='Agent Parameters')
    elevenlabs_post_call_webhook_url = fields.Char(compute='_get_post_call_webhook_url')
    display_elevenlabs_post_call_webhook_secret = fields.Char()
    elevenlabs_post_call_webhook_secret = fields.Char(groups="base.group_erp_manager")
    # Transcript elevenlabs webhook
    transcript_provider = fields.Selection(
        selection_add=[('elevenlabs', 'Elevenlabs')], ondelete={'elevenlabs': 'set default'})

    def open_elevenlabs_form(self):
        rec = self.search([])
        if not rec:
            rec = self.sudo().with_context(no_constrains=True).create({})
        else:
            rec = rec[0]
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'connect.settings',
            'res_id': rec.id,
            'name': 'ElevenLabs',
            'view_mode': 'form',
            'view_id': self.env.ref('connect_elevenlabs.connect_elevenlabs_settings_form').id,
            'target': 'current',
        }

    def _get_post_call_webhook_url(self):
        api_url = self.env['connect.settings'].sudo().get_param('api_url')
        self.elevenlabs_post_call_webhook_url = urljoin(api_url, 'connect_elevenlabs/post_call')

    def get_default_audio_source(self):
        """Prefer ElevenLabs when enabled AND a voice is configured. Otherwise
        fall back to base (twilio_tts) so the audio is still playable.
        """
        if self.sudo().get_param('elevenlabs_enabled'):
            voice = self.sudo().get_param('elevenlabs_voice')
            if voice and voice._name == 'connect.voice':
                return 'elevenlabs_tts', voice
        return super().get_default_audio_source()

    def get_elevenlabs_client(self):
        # Take this using super access because nobody must be able to access it.
        key = self.sudo().get_param('elevenlabs_api_key')
        if not key:
            raise ValidationError('Elevenlabs API key not set!')
        return ElevenLabs(api_key=key)


    def elevenlabs_get_voices(self):
        self.env['connect.voice'].get_voices()


    def elevenlabs_regenerate_prompts(self):
        # Drop all utterances; next playback regenerates them on demand.
        self.env['connect.audio.utterance'].sudo().search([
            ('source_used', '=', 'elevenlabs_tts'),
        ]).unlink()
        self.connect_notify('Prompt utterances cleared; will regenerate on next playback',
            title='ElevenLabs', notify_uid=self.env.user.id)

    def elevenlabs_regenerate_system_messages(self):
        # Drop utterances for system audios; next render regenerates.
        sysmsgs = self.env['connect.audio'].sudo().search([('system_key', '!=', False)])
        sysmsgs.utterance_ids.unlink()
        self.connect_notify('System messages cleared; will regenerate on next playback',
            title='ElevenLabs', notify_uid=self.env.user.id)


    def elevenlabs_sync_ai_agents(self):
        self.env['connect.elevenlabs_agent'].sync()


    def elevenlabs_reset_token(self):
        # Generate new token.
        self.set_param('elevenlabs_agent_token', str(uuid.uuid4()))


    def elevenlabs_sync_tools(self):
        """Sync all tools to ElevenLabs (create or update).
        System tools are excluded — they are managed via agent config (PATCH agent)."""
        tools = self.env['connect.elevenlabs_agent_tool'].search([('tool_type', '!=', 'system')])
        for tool in tools:
            if tool.tool_id:
                tool.update_elevenlabs_tool()
            else:
                tool._sync_to_elevenlabs()
        self.connect_notify('Tools sync done!', title='Elevenlabs Agent', notify_uid=self.env.user.id)

    def elevenlabs_sync(self):
        self.elevenlabs_get_voices()
        self.connect_notify('Voices sync done!', title='Elevenlabs Agent', notify_uid=self.env.user.id)
        self.elevenlabs_reset_token()
        # Sync tools (create new + update existing with new token)
        self.elevenlabs_sync_tools()

        for agent in self.env['connect.elevenlabs_agent'].search([]):
            agent.update_elevenlabs_agent()
        self.connect_notify('Sync done', title='Elevenlabs Agent', notify_uid=self.env.user.id)

    def elevenlabs_unbind_account(self):
        """Sync with new ElevenLabs account: clear agent and tool IDs"""
        # Clear all agent UIDs
        self.env['connect.elevenlabs_agent'].with_context(skip_elevenlabs=True).search([]).write({'agent_uid': None})
        # Clear all tool IDs
        self.env['connect.elevenlabs_agent_tool'].with_context(skip_elevenlabs=True).search([]).write(
            {'tool_id': None, 'synced': False})

        self.connect_notify('Unbind done!', title='Elevenlabs Agent', notify_uid=self.env.user.id)

    def ping_agent(self):
        self.ensure_one()
        try:
            response = requests.post(urljoin(self.elevenlabs_agent_url, '/agent/ping'), timeout=HTTP_API_TIMEOUT)
            if response.text == 'true':
                self.connect_notify('Pong', title='Elevenlabs Agent', notify_uid=self.env.user.id)
            else:
                self.connect_notify('Error! Check the Agent error log.', title='Elevenlabs Agent', notify_uid=self.env.user.id)
        except Exception as e:
            raise ValidationError(str(e))
