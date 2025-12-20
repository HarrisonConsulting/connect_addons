# -*- coding: utf-8 -*-
import json
import logging
from datetime import datetime

import requests

from odoo import models, fields, release, api, tools
from twilio.twiml.voice_response import VoiceResponse, Connect
from odoo.addons.connect.models.settings import debug
from odoo.addons.connect.models.twiml import pretty_xml
from odoo.exceptions import ValidationError, UserError
from elevenlabs import ConversationConfig

# Supress a warning message.
import warnings
from pydantic.warnings import PydanticDeprecatedSince20

warnings.filterwarnings("ignore", category=PydanticDeprecatedSince20)

logger = logging.getLogger(__name__)

default_prompt = """
You are Harper, a vibrant and personable sales consultant with a passion for Conversational AI systems.
"""

language_list = [
    ('ar', 'Arabic'),
    ('bg', 'Bulgarian'),
    ('zh', 'Chinese'),
    ('hr', 'Croatian'),
    ('cs', 'Czech'),
    ('da', 'Danish'),
    ('nl', 'Dutch'),
    ('en', 'English'),
    ('fi', 'Finnish'),
    ('fr', 'French'),
    ('de', 'German'),
    ('el', 'Greek'),
    ('hi', 'Hindi'),
    ('hu', 'Hungarian'),
    ('id', 'Indonesian'),
    ('it', 'Italian'),
    ('ja', 'Japanese'),
    ('ko', 'Korean'),
    ('ms', 'Malay'),
    ('no', 'Norwegian'),
    ('pl', 'Polish'),
    ('pt-br', 'Portuguese (Brazil)'),
    ('pt', 'Portuguese (Portugal)'),
    ('ro', 'Romanian'),
    ('ru', 'Russian'),
    ('sk', 'Slovak'),
    ('es', 'Spanish'),
    ('sv', 'Swedish'),
    ('ta', 'Tamil'),
    ('tr', 'Turkish'),
    ('uk', 'Ukrainian'),
    ('vi', 'Vietnamese'),
]


llm_list = [
    # GPT Models
    ('gpt-4o-mini', 'GPT 4o Mini'),
    ('gpt-4o', 'GPT 4o'),
    ('gpt-4-turbo', 'GPT 4 Turbo'),
    ('gpt-4.1', 'GPT 4.1'),
    ('gpt-4.1-mini', 'GPT 4.1 Mini'),
    ('gpt-4.1-nano', 'GPT 4.1 Nano'),
    ('gpt-5', 'GPT 5'),
    ('gpt-5.1', 'GPT 5.1'),
    ('gpt-5-mini', 'GPT 5 Mini'),
    ('gpt-5-nano', 'GPT 5 Nano'),
    # Claude Models
    ('claude-sonnet-4-5', 'Claude 4.5 Sonnet'),
    ('claude-sonnet-4', 'Claude 4 Sonnet'),
    ('claude-haiku-4-5', 'Claude 4.5 Haiku'),
    ('claude-3-7-sonnet', 'Claude 3.7 Sonnet'),
    ('claude-3-5-sonnet', 'Claude 3.5 Sonnet'),
    ('claude-3-haiku', 'Claude 3 Haiku'),
    # Gemini Models
    ('gemini-2.5-flash', 'Gemini 2.5 Flash'),
    ('gemini-2.0-flash', 'Gemini 2.0 Flash'),
    ('gemini-2.0-flash-lite', 'Gemini 2.0 Flash Lite'),
    ('gemini-1.5-pro', 'Gemini 1.5 Pro'),
    ('gemini-1.5-flash', 'Gemini 1.5 Flash'),
    ('gemini-3-pro-preview', 'Gemini 3 Pro Preview'),
    # Other Models
    ('grok-beta', 'Grok Beta'),
    ('qwen3-4b', 'Qwen 3 4B'),
    ('qwen3-30b-a3b', 'Qwen 3 30B'),
    ('custom-llm', 'Custom LLM'),
]

TURN_EAGERNESS_LIST = [
    ('patient', 'Patient'),
    ('normal', 'Normal'),
    ('eager', 'Eager'),
]

TTS_MODEL_LIST = [
    ('eleven_flash_v2_5', 'Flash v2.5 (Fastest)'),
    ('eleven_flash_v2', 'Flash v2'),
    ('eleven_turbo_v2_5', 'Turbo v2.5'),
    ('eleven_turbo_v2', 'Turbo v2'),
    ('eleven_multilingual_v2', 'Multilingual v2'),
]

AUDIO_FORMAT_LIST = [
    ('ulaw_8000', 'μ-law 8kHz (Telephony)'),
    ('pcm_8000', 'PCM 8kHz'),
    ('pcm_16000', 'PCM 16kHz'),
    ('pcm_22050', 'PCM 22kHz'),
    ('pcm_24000', 'PCM 24kHz'),
    ('pcm_44100', 'PCM 44.1kHz'),
    ('pcm_48000', 'PCM 48kHz'),
]


class ElevenlabsAgent(models.Model):
    _name = 'connect.elevenlabs_agent'
    _description = 'ElevenLabs Conversational AI Agent'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name'

    # === Identity ===
    name = fields.Char(required=True, tracking=True, help="Agent display name")
    active = fields.Boolean(default=True, tracking=True)
    agent_uid = fields.Char(
        string="ElevenLabs Agent ID",
        readonly=True,
        copy=False,
        help="Unique identifier from ElevenLabs. Auto-generated on sync.",
    )

    # === Sync Status ===
    sync_status = fields.Selection([
        ('draft', 'Not Synced'),
        ('synced', 'Synced'),
        ('modified', 'Modified'),
        ('error', 'Sync Error'),
    ], default='draft', tracking=True, help="Synchronization status with ElevenLabs")
    sync_error = fields.Text(readonly=True, help="Last sync error message")
    last_sync = fields.Datetime(readonly=True, help="Last successful sync timestamp")
    elevenlabs_archived = fields.Boolean(
        string="Archived in ElevenLabs",
        default=False,
        help="If true, agent is archived in ElevenLabs (hidden but not deleted)",
    )

    # === Prompt Configuration ===
    first_message = fields.Char(
        default="Hi there! How could I help you today?",
        required=True,
        translate=True,
        tracking=True,
        help="The greeting message the agent speaks first",
    )
    prompt = fields.Html(
        required=True,
        default=default_prompt,
        tracking=True,
        help="System prompt that defines the agent's personality and behavior",
    )

    # === Voice & Language ===
    voice = fields.Many2one(
        'connect.elevenlabs_voice',
        required=True,
        tracking=True,
        help="ElevenLabs voice for text-to-speech",
    )
    language = fields.Selection(
        selection=language_list,
        default='en',
        required=True,
        tracking=True,
        help="Primary language for the agent",
    )
    additional_languages = fields.Many2many(
        'res.lang',
        domain=[('active', '=', True)],
        help="Additional languages the agent can switch to",
    )

    # === LLM Configuration ===
    llm = fields.Selection(
        selection=llm_list,
        string='LLM Model',
        default='gpt-4o',
        required=True,
        tracking=True,
        help="Language model powering the agent's responses",
    )
    temperature = fields.Float(
        required=True,
        default=0.7,
        help="Controls creativity/randomness (0.0 = deterministic, 1.0 = creative)",
    )
    max_tokens = fields.Integer(
        required=True,
        default=-1,
        help="Maximum response tokens. -1 for unlimited.",
    )

    # === Custom LLM Configuration (via openai_base) ===
    custom_llm_model_id = fields.Many2one(
        'openai.model',
        string="Custom LLM Model",
        help="Select a model from your configured OpenAI-compatible endpoint. "
             "Configure the endpoint in Settings > Integrations > OpenAI Integration.",
    )
    custom_llm_extra_body = fields.Text(
        string="Extra Body (JSON)",
        help="Additional JSON parameters to send with each request (e.g., {\"user_id\": \"123\"})",
    )

    # === TTS Configuration ===
    model = fields.Selection(
        selection=TTS_MODEL_LIST,
        string="TTS Model",
        required=True,
        default='eleven_flash_v2_5',
        help="Text-to-speech model. Flash is faster, Turbo has better quality.",
    )
    stability = fields.Float(
        default=0.5,
        required=True,
        help="Voice stability (0.0-1.0). Higher = more consistent, lower = more expressive.",
    )
    similarity_boost = fields.Float(
        default=0.8,
        required=True,
        help="Voice similarity (0.0-1.0). Higher = closer to original voice.",
    )
    speed = fields.Float(
        default=1.0,
        required=True,
        help="Speech speed (0.7-1.2). 1.0 is normal speed.",
    )
    optimize_streaming_latency = fields.Integer(
        default=3,
        help="Latency optimization (0-4). Higher = faster but lower quality.",
    )

    # === Audio Formats ===
    output_audio_format = fields.Selection(
        selection=AUDIO_FORMAT_LIST,
        required=True,
        default='ulaw_8000',
        help="Audio format for agent speech output",
    )
    user_input_audio_format = fields.Selection(
        selection=AUDIO_FORMAT_LIST,
        required=True,
        default='ulaw_8000',
        help="Expected audio format from user input",
    )

    # === Turn/Conversation Settings ===
    turn_timeout = fields.Float(
        default=7.0,
        required=True,
        help="Seconds to wait for user response before agent continues",
    )
    turn_eagerness = fields.Selection(
        selection=TURN_EAGERNESS_LIST,
        default='normal',
        help="How quickly the agent responds. Eager = faster interruption handling.",
    )
    silence_end_call_timeout = fields.Integer(
        required=True,
        default=10,
        help="Seconds of silence before ending the call",
    )
    max_duration_seconds = fields.Integer(
        default=600,
        required=True,
        help="Maximum call duration in seconds (600 = 10 minutes)",
    )

    # === Limits ===
    agent_concurrency_limit = fields.Integer(
        default=-1,
        required=True,
        help="Max concurrent conversations. -1 for unlimited.",
    )
    daily_limit = fields.Integer(
        default=100000,
        required=True,
        help="Max conversations per day",
    )

    # === Tools & Knowledge ===
    tools = fields.Many2many(
        'connect.elevenlabs_agent_tool',
        help="Custom tools/functions the agent can call",
    )
    knowledge_base_name = fields.Char(help="Name of the knowledge base document")
    knowledge_base_note = fields.Text(help="Content for the knowledge base")
    knowledge_base_id = fields.Char(readonly=True, help="ElevenLabs knowledge base ID")

    # === Telephony Integration ===
    exten = fields.Many2one('connect.exten', ondelete='set null', readonly=True)
    exten_number = fields.Char(related='exten.number', string="Extension")

    # === Computed Fields ===
    elevenlabs_url = fields.Char(
        string="ElevenLabs Dashboard",
        compute='_compute_elevenlabs_url',
        help="Direct link to agent in ElevenLabs dashboard",
    )
    conversation_count = fields.Integer(
        compute='_compute_conversation_count',
        string="Conversations",
    )

    @api.depends('agent_uid')
    def _compute_elevenlabs_url(self):
        for rec in self:
            if rec.agent_uid:
                rec.elevenlabs_url = f"https://elevenlabs.io/app/conversational-ai/agents/{rec.agent_uid}"
            else:
                rec.elevenlabs_url = False

    def _compute_conversation_count(self):
        # TODO: Implement when call tracking is added
        for rec in self:
            rec.conversation_count = 0

    @api.model_create_multi
    def create(self, vals_list):
        res = super().create(vals_list)
        if not self.env.context.get('skip_elevenlabs'):
            for rec in res:
                try:
                    rec.create_elevenlabs_knowledge_base()
                    agent = rec.create_elevenlabs_agent()
                    rec.with_context(skip_elevenlabs=True).write({
                        'agent_uid': agent.agent_id,
                        'sync_status': 'synced',
                        'last_sync': fields.Datetime.now(),
                        'sync_error': False,
                    })
                    rec.update_elevenlabs_agent()
                except Exception as e:
                    logger.exception("Error creating ElevenLabs agent: %s", e)
                    rec.with_context(skip_elevenlabs=True).write({
                        'sync_status': 'error',
                        'sync_error': str(e),
                    })
        return res

    def write(self, vals):
        # Skip syncing for certain field updates
        skip_sync_fields = {'exten', 'sync_status', 'sync_error', 'last_sync', 'agent_uid'}
        if skip_sync_fields.issuperset(vals.keys()):
            return super().write(vals)

        res = super().write(vals)

        if not self.env.context.get('skip_elevenlabs'):
            for rec in self:
                try:
                    # Handle knowledge base updates
                    if ('knowledge_base_note' in vals or 'knowledge_base_name' in vals) and rec.knowledge_base_note:
                        rec.update_elevenlabs_knowledge_base()
                    elif not rec.knowledge_base_note and rec.knowledge_base_id:
                        rec.delete_elevenlabs_knowledge_base()

                    # Update agent in ElevenLabs
                    if rec.agent_uid:
                        rec.update_elevenlabs_agent()
                        rec.with_context(skip_elevenlabs=True).write({
                            'sync_status': 'synced',
                            'last_sync': fields.Datetime.now(),
                            'sync_error': False,
                        })
                except Exception as e:
                    logger.exception("Error syncing agent to ElevenLabs: %s", e)
                    rec.with_context(skip_elevenlabs=True).write({
                        'sync_status': 'error',
                        'sync_error': str(e),
                    })
        return res

    def unlink(self):
        for rec in self:
            try:
                if rec.agent_uid:
                    rec.delete_elevenlabs_agent()
                if rec.knowledge_base_id:
                    rec.delete_elevenlabs_knowledge_base()
            except Exception as e:
                logger.exception("Error deleting ElevenLabs agent %s: %s", rec.name, e)
        return super().unlink()

    # === Constraints ===
    @api.constrains('temperature')
    def _check_temperature(self):
        for rec in self:
            if rec.temperature < 0 or rec.temperature > 1.0:
                raise ValidationError('Temperature must be between 0.0 and 1.0.')

    @api.constrains('stability')
    def _check_stability(self):
        for rec in self:
            if rec.stability < 0 or rec.stability > 1.0:
                raise ValidationError('Stability must be between 0.0 and 1.0.')

    @api.constrains('speed')
    def _check_speed(self):
        for rec in self:
            if rec.speed < 0.7 or rec.speed > 1.2:
                raise ValidationError('Speed must be between 0.7 and 1.2.')

    @api.constrains('similarity_boost')
    def _check_similarity_boost(self):
        for rec in self:
            if rec.similarity_boost < 0 or rec.similarity_boost > 1.0:
                raise ValidationError('Similarity boost must be between 0.0 and 1.0.')

    @api.constrains('optimize_streaming_latency')
    def _check_optimize_streaming_latency(self):
        for rec in self:
            if rec.optimize_streaming_latency < 0 or rec.optimize_streaming_latency > 4:
                raise ValidationError('Streaming latency optimization must be between 0 and 4.')

    # === Sync Methods ===
    @api.model
    def sync(self):
        """Import agents from ElevenLabs into Odoo.

        This method fetches all agents from ElevenLabs API and creates/updates
        corresponding records in Odoo. Agents are matched by agent_uid.
        """
        client = self.env['connect.settings'].get_elevenlabs_client()
        try:
            response = client.conversational_ai.get_agents()
            agents = response.agents if hasattr(response, 'agents') else []
        except Exception as e:
            raise UserError(f"Failed to fetch agents from ElevenLabs: {e}")

        created_count = 0
        updated_count = 0
        errors = []

        # Get existing agents by uid
        existing_uids = {a.agent_uid: a for a in self.search([('agent_uid', '!=', False)])}

        for el_agent in agents:
            try:
                agent_uid = el_agent.agent_id
                # Fetch full agent details
                full_agent = client.conversational_ai.get_agent(agent_id=agent_uid)

                # Prepare values from ElevenLabs data
                vals = self._prepare_vals_from_elevenlabs(full_agent)

                if agent_uid in existing_uids:
                    # Update existing
                    existing_uids[agent_uid].with_context(skip_elevenlabs=True).write(vals)
                    updated_count += 1
                    logger.info("Updated agent from ElevenLabs: %s", vals.get('name'))
                else:
                    # Create new
                    vals['agent_uid'] = agent_uid
                    vals['sync_status'] = 'synced'
                    vals['last_sync'] = fields.Datetime.now()
                    self.with_context(skip_elevenlabs=True).create(vals)
                    created_count += 1
                    logger.info("Imported agent from ElevenLabs: %s", vals.get('name'))

            except Exception as e:
                error_msg = f"Agent {getattr(el_agent, 'name', agent_uid)}: {e}"
                errors.append(error_msg)
                logger.exception("Error importing agent: %s", error_msg)

        # Prepare result message
        msg_parts = []
        if created_count:
            msg_parts.append(f"Created {created_count} agent(s)")
        if updated_count:
            msg_parts.append(f"Updated {updated_count} agent(s)")
        if errors:
            msg_parts.append(f"Errors: {len(errors)}")

        result_msg = ". ".join(msg_parts) if msg_parts else "No agents found"

        self.env['connect.settings'].connect_notify(
            result_msg,
            title='ElevenLabs Agent Sync',
            notify_uid=self.env.user.id,
        )

        return {
            'created': created_count,
            'updated': updated_count,
            'errors': errors,
        }

    def _prepare_vals_from_elevenlabs(self, el_agent):
        """Convert ElevenLabs agent data to Odoo field values."""
        vals = {
            'name': el_agent.name,
            'sync_status': 'synced',
            'last_sync': fields.Datetime.now(),
            'sync_error': False,
        }

        # Extract conversation config
        config = el_agent.conversation_config
        if config:
            agent_config = getattr(config, 'agent', None)
            if agent_config:
                vals['first_message'] = getattr(agent_config, 'first_message', '') or ''
                vals['language'] = getattr(agent_config, 'language', 'en') or 'en'

                # Prompt config
                prompt_config = getattr(agent_config, 'prompt', None)
                if prompt_config:
                    vals['prompt'] = getattr(prompt_config, 'prompt', '') or ''
                    llm = getattr(prompt_config, 'llm', 'gpt-4o')
                    # Validate LLM is in our list
                    if llm and any(llm == l[0] for l in llm_list):
                        vals['llm'] = llm
                    vals['temperature'] = getattr(prompt_config, 'temperature', 0.7) or 0.7
                    vals['max_tokens'] = getattr(prompt_config, 'max_tokens', -1) or -1

            # TTS config
            tts_config = getattr(config, 'tts', None)
            if tts_config:
                voice_id = getattr(tts_config, 'voice_id', None)
                if voice_id:
                    voice = self.env['connect.elevenlabs_voice'].search([
                        ('voice_id', '=', voice_id)
                    ], limit=1)
                    if voice:
                        vals['voice'] = voice.id

                model_id = getattr(tts_config, 'model_id', 'eleven_flash_v2_5')
                if model_id and any(model_id == m[0] for m in TTS_MODEL_LIST):
                    vals['model'] = model_id

                vals['stability'] = getattr(tts_config, 'stability', 0.5) or 0.5
                vals['similarity_boost'] = getattr(tts_config, 'similarity_boost', 0.8) or 0.8
                vals['speed'] = getattr(tts_config, 'speed', 1.0) or 1.0

            # Turn config
            turn_config = getattr(config, 'turn', None)
            if turn_config:
                vals['turn_timeout'] = getattr(turn_config, 'turn_timeout', 7.0) or 7.0
                vals['silence_end_call_timeout'] = getattr(turn_config, 'silence_end_call_timeout', 10) or 10

            # Conversation config
            conv_config = getattr(config, 'conversation', None)
            if conv_config:
                vals['max_duration_seconds'] = getattr(conv_config, 'max_duration_seconds', 600) or 600

            # ASR config
            asr_config = getattr(config, 'asr', None)
            if asr_config:
                user_format = getattr(asr_config, 'user_input_audio_format', 'ulaw_8000')
                if user_format and any(user_format == f[0] for f in AUDIO_FORMAT_LIST):
                    vals['user_input_audio_format'] = user_format

        # Platform settings
        platform = el_agent.platform_settings
        if platform:
            vals['elevenlabs_archived'] = getattr(platform, 'archived', False) or False
            call_limits = getattr(platform, 'call_limits', None)
            if call_limits:
                vals['agent_concurrency_limit'] = getattr(call_limits, 'agent_concurrency_limit', -1) or -1
                vals['daily_limit'] = getattr(call_limits, 'daily_limit', 100000) or 100000

        return vals

    def action_sync_to_elevenlabs(self):
        """Manually sync this agent to ElevenLabs."""
        self.ensure_one()
        if not self.agent_uid:
            # Create new agent in ElevenLabs
            try:
                self.create_elevenlabs_knowledge_base()
                agent = self.create_elevenlabs_agent()
                self.with_context(skip_elevenlabs=True).write({
                    'agent_uid': agent.agent_id,
                    'sync_status': 'synced',
                    'last_sync': fields.Datetime.now(),
                    'sync_error': False,
                })
                self.update_elevenlabs_agent()
            except Exception as e:
                self.with_context(skip_elevenlabs=True).write({
                    'sync_status': 'error',
                    'sync_error': str(e),
                })
                raise UserError(f"Failed to create agent in ElevenLabs: {e}")
        else:
            # Update existing
            try:
                self.update_elevenlabs_agent()
                self.with_context(skip_elevenlabs=True).write({
                    'sync_status': 'synced',
                    'last_sync': fields.Datetime.now(),
                    'sync_error': False,
                })
            except Exception as e:
                self.with_context(skip_elevenlabs=True).write({
                    'sync_status': 'error',
                    'sync_error': str(e),
                })
                raise UserError(f"Failed to sync agent to ElevenLabs: {e}")

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sync Complete',
                'message': f'Agent "{self.name}" synced to ElevenLabs',
                'type': 'success',
            },
        }

    def action_open_elevenlabs(self):
        """Open agent in ElevenLabs dashboard."""
        self.ensure_one()
        if not self.elevenlabs_url:
            raise UserError("Agent not yet synced to ElevenLabs")
        return {
            'type': 'ir.actions.act_url',
            'url': self.elevenlabs_url,
            'target': 'new',
        }

    def create_extension(self):
        self.ensure_one()
        return self.env['connect.exten'].create_extension(self, 'elevenlabs_agent')

    def render(self, request, params={}):
        self.ensure_one()
        channel_sid = request.get("CallSid")
        call_id = self.env['connect.channel'].search([('sid', '=', channel_sid)], limit=1).call.id
        elevenlabs_agent_url = self.env['connect.settings'].sudo().get_param(
            'elevenlabs_agent_url'
        ).replace('https://', 'wss://').rstrip('/')
        agent_uid = self.agent_uid
        connect = Connect()
        connect.stream(
            url=f"{elevenlabs_agent_url}/twilio/stream/{agent_uid}/{call_id}/{channel_sid}",
        )
        response = VoiceResponse()
        response.append(connect)
        debug(self, pretty_xml(response))
        return response

    def transfer_test(self):
        client = self.env['connect.settings'].get_client()
        call = client.calls.create(
            to='+18109578170',
            from_='+18109578170',
            twiml="""<Response>
                <Pause length="1"/>
                <Connect>
                    <Stream url="wss://740e-2001-19f0-7400-1cfe-5400-4ff-fec7-4bbd.ngrok-free.app/twilio/stream/agent_01jvf4w2mretqvv55sxy0h50np/237/CA8ba5afd6763d6b6298500e6f96c66c14"/>
                </Connect>
            </Response> """
        )

    @api.model
    def transfer(self, params):
        channel_sid = params['channel_sid']
        exten = params['exten'] or params['default_exten']
        self = self.sudo()
        client = self.env['connect.settings'].get_client()
        channel = self.env['connect.channel'].search([('sid', '=', channel_sid)])
        exten = self.env['connect.exten'].search([('number', '=', exten)])
        if not exten:
            return 'Extension not found, please try again.'
        twiml = exten.render({
            'Caller': channel.caller,
            'Called': channel.called,
            'CallSid': channel.sid,
        })
        debug(self, 'Transfer to: {}'.format(pretty_xml(twiml)))
        client.calls(channel_sid).update(twiml=twiml)
        return True

    def create_elevenlabs_knowledge_base(self):
        if self.knowledge_base_note:
            client = self.env['connect.settings'].get_elevenlabs_client()
            knowledge_base = client.conversational_ai.create_knowledge_base_text_document(
                text=self.knowledge_base_note, name=self.knowledge_base_name)
            if knowledge_base:
                self.with_context(skip_elevenlabs=True).write({'knowledge_base_id': knowledge_base.id})
            return knowledge_base.id
        return None

    def update_elevenlabs_knowledge_base(self):
        if self.knowledge_base_note and self.knowledge_base_id:
            key = self.env['connect.settings'].sudo().get_param('elevenlabs_api_key')
            url = f"https://api.elevenlabs.io/v1/convai/knowledge-base/{self.knowledge_base_id}"
            headers = {"Content-Type": "application/json", "xi-api-key": key}
            payload = {'name': self.knowledge_base_name, 'text': self.knowledge_base_note}
            response = requests.patch(url, headers=headers, json=payload)
        elif self.knowledge_base_note and not self.knowledge_base_id:
            self.create_elevenlabs_knowledge_base()
        return True

    def delete_elevenlabs_knowledge_base(self):
        if self.knowledge_base_id:
            key = self.env['connect.settings'].sudo().get_param('elevenlabs_api_key')
            url = f"https://api.elevenlabs.io/v1/convai/knowledge-base/{self.knowledge_base_id}"
            headers = {"Content-Type": "application/json", "xi-api-key": key}
            response = requests.delete(url, headers=headers)
            # client = self.env['connect.settings'].get_elevenlabs_client()
            # client.conversational_ai.delete_knowledge_base_document(documentation_id=self.knowledge_base_id)
            self.with_context(skip_elevenlabs=True).write({'knowledge_base_id': None})

    def create_elevenlabs_agent(self):
        client = self.env['connect.settings'].get_elevenlabs_client()
        return client.conversational_ai.create_agent(
            name=self.name,
            conversation_config=ConversationConfig(),
            platform_settings=self.compute_platform_settings(),
        )

    def update_elevenlabs_agent(self):
        """Push agent configuration to ElevenLabs."""
        client = self.env['connect.settings'].get_elevenlabs_client()
        client.conversational_ai.update_agent(
            agent_id=self.agent_uid,
            name=self.name,
            conversation_config=self.compute_agent_conversation_config(),
            platform_settings=self.compute_platform_settings(),
        )

    def delete_elevenlabs_agent(self):
        client = self.env['connect.settings'].get_elevenlabs_client()
        client.conversational_ai.delete_agent(
            agent_id=self.agent_uid
        )

    def compute_platform_settings(self):
        """Build platform settings for ElevenLabs agent."""
        return {
            'archived': self.elevenlabs_archived,
            'overrides': {
                'conversation_config_override': {
                    'agent': {
                        'language': True,
                    }
                },
            },
            'call_limits': {
                'agent_concurrency_limit': self.agent_concurrency_limit,
                'daily_limit': self.daily_limit,
            }
        }

    def compute_language_presets(self):
        res = {}
        # TODO: Works on Odoo 18.0, backport to older version later.
        first_message_translations = self.get_field_translations('first_message')[0]
        for trans in first_message_translations:
            if trans['lang'] not in self.additional_languages.mapped('code'):
                logger.info('Not using language %s because not included in additional_languages.',
                            trans['lang'])
                continue
            res[trans['lang'].split('_')[0]] = {
                "overrides": {
                    "agent": {
                        "first_message": trans['value'],
                    }
                }
            }
        return res

    def compute_agent_conversation_config(self, skip_tools=False):
        """Build conversation configuration for ElevenLabs agent."""
        dynamic_variable_placeholders = {}
        for tool in self.tools:
            dynamic_variable_placeholders.update(
                dict([(param.name, f'test_{param.name}') for param in tool.params if param.value_type == 'dynamic_variable']))

        previous_topics = '\nLast conversation summary {{previous_topics}}.'

        # Build prompt config
        prompt_config = {
            'max_tokens': self.max_tokens,
            'prompt': f'{tools.html2plaintext(self.prompt)}{previous_topics}',
            'llm': self.llm,
            'temperature': self.temperature,
            'knowledge_base': [{
                'type': 'text',
                'name': self.knowledge_base_name,
                'id': self.knowledge_base_id,
            }] if self.knowledge_base_note else [],
            'tools': self.compute_agent_tools() if self.tools and not skip_tools else []
        }

        # Add custom LLM configuration if using custom-llm
        if self.llm == 'custom-llm':
            custom_llm_config = self._build_custom_llm_config()
            if custom_llm_config:
                prompt_config['custom_llm'] = custom_llm_config

        config = {
            'agent': {
                'first_message': self.first_message,
                'language': self.language,
                'dynamic_variables': dynamic_variable_placeholders,
                'prompt': prompt_config,
            },
            'language_presets': self.compute_language_presets(),
            'asr': {
                'user_input_audio_format': self.user_input_audio_format
            },
            'conversation': {
                'max_duration_seconds': self.max_duration_seconds
            },
            'tts': {
                'agent_output_audio_format': self.output_audio_format,
                'similarity_boost': self.similarity_boost,
                'speed': self.speed,
                'stability': self.stability,
                'voice_id': self.voice.voice_id,
                'model_id': self.model,
                'optimize_streaming_latency': self.optimize_streaming_latency,
            },
            'turn': {
                'turn_timeout': self.turn_timeout,
                'silence_end_call_timeout': self.silence_end_call_timeout,
                'mode': 'turn',
                'turn_eagerness': self.turn_eagerness or 'normal',
            }
        }
        logger.debug('Agent conversation config: %s', json.dumps(config, indent=2))
        return config

    def _build_custom_llm_config(self):
        """Build custom LLM configuration from openai_base settings.

        Returns a dict compatible with ElevenLabs custom_llm schema:
        {
            'url': 'https://your-endpoint.com/v1/chat/completions',
            'model_id': 'your-model-name',
            'api_key': 'your-api-key',  # or secret reference
        }
        """
        IrConfigParameter = self.env['ir.config_parameter'].sudo()

        # Get OpenAI base configuration
        base_url = IrConfigParameter.get_param('openai.base_url', '')
        api_key = IrConfigParameter.get_param('openai.api_key', '')

        if not base_url:
            logger.warning("Custom LLM selected but no OpenAI base URL configured")
            return None

        # Ensure URL ends with /v1/chat/completions for ElevenLabs
        if not base_url.endswith('/'):
            base_url = base_url + '/'
        if not base_url.endswith('v1/'):
            base_url = base_url + 'v1/'
        chat_url = base_url + 'chat/completions'

        config = {
            'url': chat_url,
        }

        # Add model ID if a custom model is selected
        if self.custom_llm_model_id:
            config['model_id'] = self.custom_llm_model_id.model_id

        # Add API key if configured
        if api_key:
            config['api_key'] = api_key

        # Add extra body parameters if configured
        if self.custom_llm_extra_body:
            try:
                extra_body = json.loads(self.custom_llm_extra_body)
                if extra_body:
                    config['extra_body'] = extra_body
            except json.JSONDecodeError:
                logger.warning("Invalid JSON in custom_llm_extra_body, ignoring")

        return config

    def compute_agent_tools(self):
        tools = []
        for tool in self.tools:
            if not tool.is_enabled:
                continue
            dynamic_variables_placeholders = dict(
                [(param.name, f'test_{param.name}') for param in tool.params if param.value_type == 'dynamic_variable'])
            if tool.tool_type == 'client':
                tool_config = {
                    'type': tool.tool_type,
                    'description': tool.description,
                    'name': tool.name,
                    'dynamic_variables': dynamic_variables_placeholders,
                    'expects_response': tool.client_expects_response,
                    'parameters': {
                        "description": tool.body_params_description or '',
                        'required': [param.name for param in tool.params if param.required],
                        'properties': {
                            param.name: {
                                'type': param.data_type,
                                'description': param.description if param.value_type == 'description' else '',
                                "constant_value": param.constant_value if param.value_type == 'constant_value' else '',
                                "dynamic_variable": param.dynamic_variable if param.value_type == 'dynamic_variable' else '',
                            } for param in tool.params
                        }
                    },
                    'response_timeout_secs': tool.response_timeout_secs,
                }
                tools.append(tool_config)
            elif tool.tool_type == 'webhook':
                tool_config = {
                    'type': tool.tool_type,
                    'description': tool.description,
                    'name': tool.name,
                    'dynamic_variables': dynamic_variables_placeholders,
                    'response_timeout_secs': tool.response_timeout_secs,
                }
                tool_config.update({
                    'api_schema': {
                        'method': tool.method,
                        'url': tool.get_tool_url(),
                        'request_body_schema': {
                            "description": tool.body_params_description,
                            'required': [param.name for param in tool.params if param.required],
                            'properties': {
                                param.name: {
                                    'type': param.data_type,
                                    'description': param.description if param.value_type == 'description' else '',
                                    "constant_value": param.constant_value if param.value_type == 'constant_value' else '',
                                    "dynamic_variable": param.dynamic_variable if param.value_type == 'dynamic_variable' else '',
                                } for param in tool.params
                            }
                        },
                        'request_headers': {
                            'x-elevenlabs-agent-token': self.env['connect.settings'].get_param('elevenlabs_agent_token'),
                        }
                    },
                    'response_timeout_secs': tool.response_timeout_secs,
                })
                tools.append(tool_config)
            elif tool.tool_type == 'system':
                tool_config = {
                    'type': tool.tool_type,
                    'description': tool.description,
                    'name': tool.name,
                }
                tools.append(tool_config)
        return tools


    def print_config(self):
        client = self.env['connect.settings'].get_elevenlabs_client()
        agents = client.conversational_ai.get_agents().agents
        for agent in agents:
            agent = client.conversational_ai.get_agent(agent_id=agent.agent_uid)
            print(json.dumps(str(agent.conversation_config.agent), indent=2))
            #tools = agent.conversation_config.agent.prompt.tools
            #for tool in tools:
            #    print(tool)
