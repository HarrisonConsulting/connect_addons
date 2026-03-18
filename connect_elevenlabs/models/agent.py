# -*- coding: utf-8 -*-
import json
import logging
from datetime import datetime

# Supress a warning message.
import warnings

import requests
from elevenlabs.core.api_error import ApiError
from elevenlabs.types import AgentPlatformSettingsRequestModel, ConversationalConfig
from odoo import api, fields, models, release, tools
from odoo.addons.connect.models.settings import debug
from odoo.addons.connect.models.twiml import pretty_xml
from odoo.exceptions import UserError, ValidationError
from pydantic.warnings import PydanticDeprecatedSince20
from twilio.twiml.voice_response import Connect, VoiceResponse

warnings.filterwarnings("ignore", category=PydanticDeprecatedSince20)

logger = logging.getLogger(__name__)

default_prompt = """
You are Harper, a vibrant and personable sales consultant with a passion for Conversational AI systems.
"""

language_list = [
    ("ar", "Arabic"),
    ("bg", "Bulgarian"),
    ("zh", "Chinese"),
    ("hr", "Croatian"),
    ("cs", "Czech"),
    ("da", "Danish"),
    ("nl", "Dutch"),
    ("en", "English"),
    ("fi", "Finnish"),
    ("fr", "French"),
    ("de", "German"),
    ("el", "Greek"),
    ("hi", "Hindi"),
    ("hu", "Hungarian"),
    ("id", "Indonesian"),
    ("it", "Italian"),
    ("ja", "Japanese"),
    ("ko", "Korean"),
    ("ms", "Malay"),
    ("no", "Norwegian"),
    ("pl", "Polish"),
    ("pt-br", "Portuguese (Brazil)"),
    ("pt", "Portuguese (Portugal)"),
    ("ro", "Romanian"),
    ("ru", "Russian"),
    ("sk", "Slovak"),
    ("es", "Spanish"),
    ("sv", "Swedish"),
    ("ta", "Tamil"),
    ("tr", "Turkish"),
    ("uk", "Ukrainian"),
    ("vi", "Vietnamese"),
]

llm_list = [
    # OpenAI
    ("gpt-3.5-turbo", "GPT 3.5 Turbo"),
    ("gpt-4o-mini", "GPT 4o Mini"),
    ("gpt-4o", "GPT 4o"),
    ("gpt-4-turbo", "GPT 4 Turbo"),
    ("gpt-4", "GPT 4"),
    ("gpt-4.1", "GPT 4.1"),
    ("gpt-4.1-mini", "GPT 4.1 Mini"),
    ("gpt-4.1-nano", "GPT 4.1 Nano"),
    ("gpt-5", "GPT 5"),
    ("gpt-5-mini", "GPT 5 Mini"),
    ("gpt-5-nano", "GPT 5 Nano"),
    ("gpt-5.1", "GPT 5.1"),
    ("gpt-5.2", "GPT 5.2"),
    # Gemini (Google)
    ("gemini-1.0-pro", "Gemini 1.0 Pro"),
    ("gemini-1.5-pro", "Gemini 1.5 Pro"),
    ("gemini-1.5-flash", "Gemini 1.5 Flash"),
    ("gemini-2.0-flash-001", "Gemini 2.0 Flash 001"),
    ("gemini-2.0-flash-lite", "Gemini 2.0 Flash Lite"),
    ("gemini-2.5-flash", "Gemini 2.5 Flash"),
    ("gemini-2.5-flash-lite", "Gemini 2.5 Flash Lite"),
    ("gemini-3-pro-preview", "Gemini 3 Pro Preview"),
    ("gemini-3-flash-preview", "Gemini 3 Flash Preview"),
    # Anthropic (Claude)
    ("claude-3-5-sonnet", "Claude 3.5 Sonnet"),
    ("claude-3-5-sonnet-v1", "Claude 3.5 Sonnet v1"),
    ("claude-3-7-sonnet", "Claude 3.7 Sonnet"),
    ("claude-3-haiku", "Claude 3 Haiku"),
    ("claude-sonnet-4.5", "Claude Sonnet 4.5"),
    ("claude-sonnet-4", "Claude Sonnet 4"),
    ("claude-haiku-4.5", "Claude Haiku 4.5"),
    # ElevenLabs hosted (open-source)
    ("glm-4.5-air", "GLM 4.5 Air"),
    ("qwen3-30b-a3b", "Qwen3 30B A3B"),
    # Other
    ("grok-beta", "Grok Beta"),
    ("custom-llm", "Custom LLM"),
]

TURN_EAGERNESS_LIST = [
    ("patient", "Patient"),
    ("normal", "Normal"),
    ("eager", "Eager"),
]

TTS_MODEL_LIST = [
    ("eleven_flash_v2_5", "Flash v2.5 (Fastest)"),
    ("eleven_flash_v2", "Flash v2"),
    ("eleven_turbo_v2_5", "Turbo v2.5"),
    ("eleven_turbo_v2", "Turbo v2"),
    ("eleven_multilingual_v2", "Multilingual v2"),
]

AUDIO_FORMAT_LIST = [
    ("ulaw_8000", "ulaw 8000"),
    ("pcm_8000", "PCM 8000"),
    ("pcm_16000", "PCM 16000"),
    ("pcm_22050", "PCM 22050"),
    ("pcm_24000", "PCM 24000"),
    ("pcm_44100", "PCM 44100"),
    ("pcm_48000", "PCM 48000"),
]


class ElevenlabsAgent(models.Model):
    _name = "connect.elevenlabs_agent"
    _description = "ElevenLabs Conversational AI Agent"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"

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
    sync_status = fields.Selection(
        [
            ("draft", "Not Synced"),
            ("synced", "Synced"),
            ("modified", "Modified"),
            ("error", "Sync Error"),
        ],
        default="draft",
        tracking=True,
        help="Synchronization status with ElevenLabs",
    )
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
        help="System prompt that defines the agent's personality and behavior",
    )
    prompt_version_ids = fields.One2many(
        "connect.elevenlabs_agent_prompt",
        "agent",
        string="Prompt Versions",
    )
    active_prompt_version = fields.Many2one(
        "connect.elevenlabs_agent_prompt",
        string="Prompt Version",
        domain="[('agent', '=', id)]",
    )

    # === Voice & Language ===
    voice = fields.Many2one(
        "connect.elevenlabs_voice",
        required=True,
        tracking=True,
        help="ElevenLabs voice for text-to-speech",
    )
    language = fields.Selection(
        selection=language_list,
        default="en",
        required=True,
        tracking=True,
        help="Primary language for the agent",
    )
    additional_languages = fields.Many2many(
        "res.lang",
        domain=[("active", "=", True)],
        help="Additional languages the agent can switch to",
    )

    # === LLM Configuration ===
    llm = fields.Selection(
        selection=llm_list,
        string="LLM Model",
        default="gpt-4o",
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

    # === TTS Configuration ===
    model = fields.Selection(
        selection=TTS_MODEL_LIST,
        string="TTS Model",
        required=True,
        default="eleven_flash_v2_5",
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
        default="ulaw_8000",
        help="Audio format for agent speech output",
    )
    user_input_audio_format = fields.Selection(
        selection=AUDIO_FORMAT_LIST,
        required=True,
        default="ulaw_8000",
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
        default="normal",
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
        "connect.elevenlabs_agent_tool",
        help="Custom tools/functions the agent can call",
    )
    knowledge_base_name = fields.Char(help="Name of the knowledge base document")
    knowledge_base_note = fields.Text(help="Content for the knowledge base")
    knowledge_base_id = fields.Char(
        readonly=True, help="ElevenLabs knowledge base ID"
    )

    # === Telephony Integration ===
    exten = fields.Many2one("connect.exten", ondelete="set null", readonly=True)
    exten_number = fields.Char(related="exten.number", string="Extension")

    # === Template & Transfer (upstream architecture) ===
    template = fields.Many2one(
        "connect.elevenlabs_agent_template", ondelete="set null"
    )
    transfer_to_agent = fields.One2many(
        "connect.elevenlabs_agent_transfer", "agent"
    )
    has_transfer_tool = fields.Boolean(compute="_compute_has_transfer_tool")

    # === Computed Fields ===
    elevenlabs_url = fields.Char(
        string="ElevenLabs Dashboard",
        compute="_compute_elevenlabs_url",
        help="Direct link to agent in ElevenLabs dashboard",
    )
    conversation_count = fields.Integer(
        compute="_compute_conversation_count",
        string="Conversations",
    )

    @api.depends("agent_uid")
    def _compute_elevenlabs_url(self):
        for rec in self:
            if rec.agent_uid:
                rec.elevenlabs_url = (
                    f"https://elevenlabs.io/app/conversational-ai/agents/{rec.agent_uid}"
                )
            else:
                rec.elevenlabs_url = False

    def _compute_conversation_count(self):
        for rec in self:
            rec.conversation_count = 0

    @api.depends("tools")
    def _compute_has_transfer_tool(self):
        transfer_tool = self.env.ref(
            "connect_elevenlabs.agent_tool_transfer_to_agent",
            raise_if_not_found=False,
        )
        for rec in self:
            rec.has_transfer_tool = (
                transfer_tool in rec.tools if transfer_tool else False
            )

    @api.onchange("active_prompt_version")
    def _onchange_active_prompt_version(self):
        if self.active_prompt_version:
            self.prompt = self.active_prompt_version.prompt

    @api.onchange("template")
    def _onchange_template(self):
        if self.template:
            self.prompt = self.template.system_prompt

    # === CRUD ===

    @api.model_create_multi
    def create(self, vals_list):
        res = super().create(vals_list)
        if not self.env.context.get("skip_elevenlabs"):
            for rec in res:
                try:
                    rec.create_elevenlabs_knowledge_base()
                    agent = rec.create_elevenlabs_agent()
                    rec.with_context(skip_elevenlabs=True).write(
                        {
                            "agent_uid": agent.agent_id,
                            "sync_status": "synced",
                            "last_sync": fields.Datetime.now(),
                            "sync_error": False,
                        }
                    )
                    rec.update_elevenlabs_agent()
                except Exception as e:
                    logger.exception("Error creating ElevenLabs agent: %s", e)
                    rec.with_context(skip_elevenlabs=True).write(
                        {
                            "sync_status": "error",
                            "sync_error": str(e),
                        }
                    )
        return res

    def write(self, vals):
        # Skip syncing for certain field updates
        skip_sync_fields = {
            "exten",
            "sync_status",
            "sync_error",
            "last_sync",
            "agent_uid",
        }
        if skip_sync_fields.issuperset(vals.keys()):
            return super().write(vals)

        res = super().write(vals)

        # Handle prompt versioning
        if "prompt" in vals and "active_prompt_version" not in vals:
            for rec in self:
                version_count = len(rec.prompt_version_ids)
                version = self.env["connect.elevenlabs_agent_prompt"].create(
                    {
                        "name": f"v{version_count + 1}",
                        "agent": rec.id,
                        "prompt": vals["prompt"],
                    }
                )
                rec.with_context(skip_elevenlabs=True).write(
                    {"active_prompt_version": version.id}
                )

        if not self.env.context.get("skip_elevenlabs"):
            for rec in self:
                try:
                    # Handle knowledge base updates
                    if (
                        "knowledge_base_note" in vals
                        or "knowledge_base_name" in vals
                    ) and rec.knowledge_base_note:
                        rec.update_elevenlabs_knowledge_base()
                    elif not rec.knowledge_base_note and rec.knowledge_base_id:
                        rec.delete_elevenlabs_knowledge_base()

                    # Update agent in ElevenLabs
                    if rec.agent_uid:
                        rec.update_elevenlabs_agent()
                        rec.with_context(skip_elevenlabs=True).write(
                            {
                                "sync_status": "synced",
                                "last_sync": fields.Datetime.now(),
                                "sync_error": False,
                            }
                        )
                except Exception as e:
                    logger.exception("Error syncing agent to ElevenLabs: %s", e)
                    rec.with_context(skip_elevenlabs=True).write(
                        {
                            "sync_status": "error",
                            "sync_error": str(e),
                        }
                    )
        return res

    def unlink(self):
        for rec in self:
            try:
                if rec.agent_uid:
                    rec.delete_elevenlabs_agent()
                if rec.knowledge_base_id:
                    rec.delete_elevenlabs_knowledge_base()
            except Exception as e:
                logger.exception(
                    "Error deleting ElevenLabs agent %s: %s", rec.name, e
                )
        return super().unlink()

    # === Constraints ===

    @api.constrains("temperature")
    def _check_temperature(self):
        for rec in self:
            if rec.temperature < 0 or rec.temperature > 1.0:
                raise ValidationError("Temperature must be between 0.0 and 1.0.")

    @api.constrains("stability")
    def _check_stability(self):
        for rec in self:
            if rec.stability < 0 or rec.stability > 1.0:
                raise ValidationError("Stability must be between 0.0 and 1.0.")

    @api.constrains("speed")
    def _check_speed(self):
        for rec in self:
            if rec.speed < 0.7 or rec.speed > 1.2:
                raise ValidationError("Speed must be between 0.7 and 1.2.")

    @api.constrains("similarity_boost")
    def _check_similarity_boost(self):
        for rec in self:
            if rec.similarity_boost < 0 or rec.similarity_boost > 1.0:
                raise ValidationError(
                    "Similarity boost must be between 0.0 and 1.0."
                )

    @api.constrains("optimize_streaming_latency")
    def _check_optimize_streaming_latency(self):
        for rec in self:
            if (
                rec.optimize_streaming_latency < 0
                or rec.optimize_streaming_latency > 4
            ):
                raise ValidationError(
                    "Streaming latency optimization must be between 0 and 4."
                )

    # === Sync Methods ===

    @api.model
    def sync(self):
        """Import agents from ElevenLabs into Odoo.

        Fetches all agents from ElevenLabs API and creates/updates
        corresponding records in Odoo. Agents are matched by agent_uid.
        """
        client = self.env["connect.settings"].get_elevenlabs_client()
        try:
            response = client.conversational_ai.get_agents()
            agents = response.agents if hasattr(response, "agents") else []
        except Exception as e:
            raise UserError(f"Failed to fetch agents from ElevenLabs: {e}")

        created_count = 0
        updated_count = 0
        errors = []

        # Get existing agents by uid
        existing_uids = {
            a.agent_uid: a
            for a in self.search([("agent_uid", "!=", False)])
        }

        for el_agent in agents:
            try:
                agent_uid = el_agent.agent_id
                # Fetch full agent details
                full_agent = client.conversational_ai.get_agent(
                    agent_id=agent_uid
                )

                # Prepare values from ElevenLabs data
                vals = self._prepare_vals_from_elevenlabs(full_agent)

                if agent_uid in existing_uids:
                    existing_uids[agent_uid].with_context(
                        skip_elevenlabs=True
                    ).write(vals)
                    updated_count += 1
                    logger.info(
                        "Updated agent from ElevenLabs: %s", vals.get("name")
                    )
                else:
                    vals["agent_uid"] = agent_uid
                    vals["sync_status"] = "synced"
                    vals["last_sync"] = fields.Datetime.now()
                    self.with_context(skip_elevenlabs=True).create(vals)
                    created_count += 1
                    logger.info(
                        "Imported agent from ElevenLabs: %s", vals.get("name")
                    )

            except Exception as e:
                error_msg = (
                    f"Agent {getattr(el_agent, 'name', agent_uid)}: {e}"
                )
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

        self.env["connect.settings"].connect_notify(
            result_msg,
            title="ElevenLabs Agent Sync",
            notify_uid=self.env.user.id,
        )

        return {
            "created": created_count,
            "updated": updated_count,
            "errors": errors,
        }

    def _prepare_vals_from_elevenlabs(self, el_agent):
        """Convert ElevenLabs agent data to Odoo field values."""
        vals = {
            "name": el_agent.name,
            "sync_status": "synced",
            "last_sync": fields.Datetime.now(),
            "sync_error": False,
        }

        # Extract conversation config
        config = el_agent.conversation_config
        if config:
            agent_config = getattr(config, "agent", None)
            if agent_config:
                vals["first_message"] = (
                    getattr(agent_config, "first_message", "") or ""
                )
                vals["language"] = (
                    getattr(agent_config, "language", "en") or "en"
                )

                # Prompt config
                prompt_config = getattr(agent_config, "prompt", None)
                if prompt_config:
                    vals["prompt"] = (
                        getattr(prompt_config, "prompt", "") or ""
                    )
                    llm = getattr(prompt_config, "llm", "gpt-4o")
                    if llm and any(llm == l[0] for l in llm_list):
                        vals["llm"] = llm
                    vals["temperature"] = (
                        getattr(prompt_config, "temperature", 0.7) or 0.7
                    )
                    vals["max_tokens"] = (
                        getattr(prompt_config, "max_tokens", -1) or -1
                    )

            # TTS config
            tts_config = getattr(config, "tts", None)
            if tts_config:
                voice_id = getattr(tts_config, "voice_id", None)
                if voice_id:
                    voice = self.env["connect.elevenlabs_voice"].search(
                        [("voice_id", "=", voice_id)], limit=1
                    )
                    if voice:
                        vals["voice"] = voice.id

                model_id = getattr(
                    tts_config, "model_id", "eleven_flash_v2_5"
                )
                if model_id and any(
                    model_id == m[0] for m in TTS_MODEL_LIST
                ):
                    vals["model"] = model_id

                vals["stability"] = (
                    getattr(tts_config, "stability", 0.5) or 0.5
                )
                vals["similarity_boost"] = (
                    getattr(tts_config, "similarity_boost", 0.8) or 0.8
                )
                vals["speed"] = getattr(tts_config, "speed", 1.0) or 1.0

            # Turn config
            turn_config = getattr(config, "turn", None)
            if turn_config:
                vals["turn_timeout"] = (
                    getattr(turn_config, "turn_timeout", 7.0) or 7.0
                )
                vals["silence_end_call_timeout"] = (
                    getattr(turn_config, "silence_end_call_timeout", 10)
                    or 10
                )

            # Conversation config
            conv_config = getattr(config, "conversation", None)
            if conv_config:
                vals["max_duration_seconds"] = (
                    getattr(conv_config, "max_duration_seconds", 600) or 600
                )

            # ASR config
            asr_config = getattr(config, "asr", None)
            if asr_config:
                user_format = getattr(
                    asr_config, "user_input_audio_format", "ulaw_8000"
                )
                if user_format and any(
                    user_format == f[0] for f in AUDIO_FORMAT_LIST
                ):
                    vals["user_input_audio_format"] = user_format

        # Platform settings
        platform = el_agent.platform_settings
        if platform:
            vals["elevenlabs_archived"] = (
                getattr(platform, "archived", False) or False
            )
            call_limits = getattr(platform, "call_limits", None)
            if call_limits:
                vals["agent_concurrency_limit"] = (
                    getattr(call_limits, "agent_concurrency_limit", -1) or -1
                )
                vals["daily_limit"] = (
                    getattr(call_limits, "daily_limit", 100000) or 100000
                )

        return vals

    def action_sync_to_elevenlabs(self):
        """Manually sync this agent to ElevenLabs."""
        self.ensure_one()
        if not self.agent_uid:
            try:
                self.create_elevenlabs_knowledge_base()
                agent = self.create_elevenlabs_agent()
                self.with_context(skip_elevenlabs=True).write(
                    {
                        "agent_uid": agent.agent_id,
                        "sync_status": "synced",
                        "last_sync": fields.Datetime.now(),
                        "sync_error": False,
                    }
                )
                self.update_elevenlabs_agent()
            except Exception as e:
                self.with_context(skip_elevenlabs=True).write(
                    {
                        "sync_status": "error",
                        "sync_error": str(e),
                    }
                )
                raise UserError(
                    f"Failed to create agent in ElevenLabs: {e}"
                )
        else:
            try:
                self.update_elevenlabs_agent()
                self.with_context(skip_elevenlabs=True).write(
                    {
                        "sync_status": "synced",
                        "last_sync": fields.Datetime.now(),
                        "sync_error": False,
                    }
                )
            except Exception as e:
                self.with_context(skip_elevenlabs=True).write(
                    {
                        "sync_status": "error",
                        "sync_error": str(e),
                    }
                )
                raise UserError(
                    f"Failed to sync agent to ElevenLabs: {e}"
                )

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Sync Complete",
                "message": f'Agent "{self.name}" synced to ElevenLabs',
                "type": "success",
            },
        }

    def action_open_elevenlabs(self):
        """Open agent in ElevenLabs dashboard."""
        self.ensure_one()
        if not self.elevenlabs_url:
            raise UserError("Agent not yet synced to ElevenLabs")
        return {
            "type": "ir.actions.act_url",
            "url": self.elevenlabs_url,
            "target": "new",
        }

    # === Extension & Rendering ===

    def create_extension(self):
        self.ensure_one()
        return self.env["connect.exten"].create_extension(
            self, "elevenlabs_agent"
        )

    def render(self, request, params={}):
        self.ensure_one()
        channel_sid = request.get("CallSid")
        call_id = (
            self.env["connect.channel"]
            .search([("sid", "=", channel_sid)], limit=1)
            .call.id
        )
        elevenlabs_agent_url = (
            self.env["connect.settings"]
            .sudo()
            .get_param("elevenlabs_agent_url")
            .replace("https://", "wss://")
            .rstrip("/")
        )
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
        client = self.env["connect.settings"].get_client()
        call = client.calls.create(
            to="+18109578170",
            from_="+18109578170",
            twiml="""<Response>
                <Pause length="1"/>
                <Connect>
                    <Stream url="wss://740e-2001-19f0-7400-1cfe-5400-4ff-fec7-4bbd.ngrok-free.app/twilio/stream/agent_01jvf4w2mretqvv55sxy0h50np/237/CA8ba5afd6763d6b6298500e6f96c66c14"/>
                </Connect>
            </Response> """,
        )

    # === Transfer ===

    @api.model
    def transfer(self, channel_sid=None, exten=None):
        logger.info(
            f"Transfer request: exten={exten}, channel_sid={channel_sid}"
        )
        if not channel_sid or not exten:
            return "Not all parameters passed. You must provide channel_sid and exten (only digits)"
        if isinstance(exten, str) and not exten.isalnum():
            return "Wrong extension format. Only digits, e.g. 101"
        self = self.sudo()
        client = self.env["connect.settings"].get_client()
        channel = self.env["connect.channel"].search(
            [("sid", "=", channel_sid)]
        )
        exten_rec = self.env["connect.exten"].search(
            [("number", "=", str(exten).strip())]
        )
        if not exten_rec:
            # Get all published extensions
            published_extens = self.env["connect.exten"].search(
                [("is_published", "=", True)]
            )
            if published_extens:
                available = ", ".join(
                    [
                        '<{}> "{}"'.format(
                            k.number, k.dst.name if k.dst else ""
                        )
                        for k in published_extens
                    ]
                )
                if len(published_extens) == 1:
                    exten_rec = published_extens[0]
                    logger.info(
                        "Extension %s not found, falling back to single published extension %s",
                        exten,
                        exten_rec.number,
                    )
                else:
                    return f"Extension {exten} not found. Available extensions: {available}. Please try again with a correct number."
            else:
                return "There is no public extension to connect the call. Cannot transfer"
        exten = exten_rec
        twiml = exten.render(
            {
                "Caller": channel.caller,
                "Called": channel.called,
                "CallSid": channel.sid,
            }
        )
        debug(self, "Transfer to: {}".format(pretty_xml(twiml)))
        client.calls(channel_sid).update(twiml=twiml)
        return "Transfer Successful"

    # === Knowledge Base ===

    def create_elevenlabs_knowledge_base(self):
        if self.knowledge_base_note:
            client = self.env["connect.settings"].get_elevenlabs_client()
            knowledge_base = (
                client.conversational_ai.create_knowledge_base_text_document(
                    text=self.knowledge_base_note,
                    name=self.knowledge_base_name,
                )
            )
            if knowledge_base:
                self.with_context(skip_elevenlabs=True).write(
                    {"knowledge_base_id": knowledge_base.id}
                )
            return knowledge_base.id
        return None

    def update_elevenlabs_knowledge_base(self):
        if self.knowledge_base_note and self.knowledge_base_id:
            key = (
                self.env["connect.settings"]
                .sudo()
                .get_param("elevenlabs_api_key")
            )
            url = f"https://api.elevenlabs.io/v1/convai/knowledge-base/{self.knowledge_base_id}"
            headers = {"Content-Type": "application/json", "xi-api-key": key}
            payload = {
                "name": self.knowledge_base_name,
                "text": self.knowledge_base_note,
            }
            requests.patch(url, headers=headers, json=payload)
        elif self.knowledge_base_note and not self.knowledge_base_id:
            self.create_elevenlabs_knowledge_base()
        return True

    def delete_elevenlabs_knowledge_base(self):
        if self.knowledge_base_id:
            key = (
                self.env["connect.settings"]
                .sudo()
                .get_param("elevenlabs_api_key")
            )
            url = f"https://api.elevenlabs.io/v1/convai/knowledge-base/{self.knowledge_base_id}"
            headers = {"Content-Type": "application/json", "xi-api-key": key}
            requests.delete(url, headers=headers)
            self.with_context(skip_elevenlabs=True).write(
                {"knowledge_base_id": None}
            )

    # === ElevenLabs Agent CRUD ===

    def create_elevenlabs_agent(self):
        client = self.env["connect.settings"].get_elevenlabs_client()
        try:
            conversation_config = self._build_conversational_config()
            return client.conversational_ai.agents.create(
                name=self.name,
                conversation_config=conversation_config,
                platform_settings=self._build_platform_settings(),
            )
        except Exception as e:
            logger.exception("Error creating ElevenLabs agent: %s", e)
            raise

    def update_elevenlabs_agent(self):
        """Push agent configuration to ElevenLabs."""
        client = self.env["connect.settings"].get_elevenlabs_client()
        try:
            conversation_config = self._build_conversational_config()
            client.conversational_ai.agents.update(
                agent_id=self.agent_uid,
                name=self.name,
                conversation_config=conversation_config,
                platform_settings=self._build_platform_settings(),
            )
        except ApiError as e:
            if e.status_code == 404:
                logger.exception("Agent doesn't exist! Create.")
                agent = self.create_elevenlabs_agent()
                self.with_context(skip_elevenlabs=True).write(
                    {"agent_uid": agent.agent_id}
                )
                self.update_elevenlabs_agent()
            else:
                logger.exception("Error updating ElevenLabs agent: %s", e)
                raise
        except Exception as e:
            logger.exception("Error updating ElevenLabs agent: %s", e)
            raise

    def delete_elevenlabs_agent(self):
        client = self.env["connect.settings"].get_elevenlabs_client()
        try:
            client.conversational_ai.agents.delete(agent_id=self.agent_uid)
        except Exception as e:
            logger.exception("Error deleting ElevenLabs agent: %s", e)
            raise

    # === Config Building ===

    def _build_conversational_config(self) -> ConversationalConfig:
        """Build ConversationalConfig using SDK types for ElevenLabs API."""

        # Build dynamic variable placeholders from tools
        dynamic_variable_placeholders = {}
        for tool in self.tools:
            dynamic_variable_placeholders.update(
                dict(
                    [
                        (param.name, f"test_{param.name}")
                        for param in tool.params
                        if param.value_type == "dynamic_variable"
                    ]
                )
            )

        agent_dict = {
            "first_message": self.first_message,
            "language": self.language,
        }

        if dynamic_variable_placeholders:
            agent_dict["dynamic_variables"] = dynamic_variable_placeholders

        agent_dict["prompt"] = self._compute_prompt_config()

        # Build ASR config
        asr_dict = {"user_input_audio_format": self.user_input_audio_format}

        # Build TTS config
        tts_dict = {
            "agent_output_audio_format": self.output_audio_format,
            "similarity_boost": self.similarity_boost,
            "speed": self.speed,
            "stability": self.stability,
            "voice_id": self.voice.voice_id,
            "model_id": self.model,
            "optimize_streaming_latency": self.optimize_streaming_latency,
        }

        # Build conversation config
        conversation_dict = {"max_duration_seconds": self.max_duration_seconds}

        # Build turn config
        turn_dict = {
            "turn_timeout": self.turn_timeout,
            "silence_end_call_timeout": self.silence_end_call_timeout,
            "mode": "turn",
            "turn_eagerness": self.turn_eagerness or "normal",
        }

        # Build language presets
        language_presets = {}
        first_message_translations = self.get_field_translations(
            "first_message"
        )[0]
        for trans in first_message_translations:
            if (
                trans["lang"]
                not in self.additional_languages.mapped("code")
            ):
                logger.info(
                    "Not using language %s because not included in additional_languages.",
                    trans["lang"],
                )
                continue
            lang_code = trans["lang"].split("_")[0]
            language_presets[lang_code] = {
                "overrides": {
                    "agent": {
                        "first_message": trans["value"],
                    }
                }
            }

        # Return complete ConversationalConfig using dict structure
        config_dict = {
            "agent": agent_dict,
            "asr": asr_dict,
            "tts": tts_dict,
            "conversation": conversation_dict,
            "turn": turn_dict,
        }

        if language_presets:
            config_dict["language_presets"] = language_presets

        try:
            return ConversationalConfig(**config_dict)
        except Exception as e:
            logger.warning(
                "Standard validation failed for ConversationalConfig: %s. Using model_construct.",
                e,
            )
            return ConversationalConfig.model_construct(**config_dict)

    def _compute_built_in_tools(self):
        built_in_tools = {}
        for tool in self.tools:
            if tool.tool_type != "system":
                continue

            tool_config = {
                "type": "system",
                "name": tool.name,
                "description": tool.description or "",
                "params": {"system_tool_type": tool.name},
                "disable_interruptions": tool.disable_interruptions,
                "tool_error_handling_mode": "auto",
            }

            if tool.name == "transfer_to_agent":
                transfers = []
                for transfer in self.transfer_to_agent:
                    transfers.append(
                        {
                            "agent_id": transfer.transfer_to_agent.agent_uid,
                            "condition": transfer.condition,
                            "enable_transferred_agent_first_message": True,
                        }
                    )
                tool_config["params"]["transfers"] = transfers
            elif tool.name == "voicemail_detection":
                tool_config["params"]["voicemail_message"] = (
                    tool.voicemail_message or ""
                )
            elif tool.name == "play_keypad_touch_tone":
                tool_config["params"]["use_out_of_band_dtmf"] = (
                    tool.use_out_of_band_dtmf
                )

            built_in_tools[tool.name] = tool_config

        return built_in_tools

    def _compute_prompt_config(self):
        previous_topics = (
            "\n\nLast conversation summary {{previous_topics}}."
        )
        published_extensions = "\n\nCALL TRANSFER INFORMATION\n- Available extensions: {{available_extensions}}"
        prompt_text = tools.html2plaintext(self.prompt) if self.prompt else ""
        agent_prompt = f"{prompt_text}{previous_topics}{published_extensions}"

        prompt_dict = {
            "prompt": agent_prompt,
            "llm": self.llm,
            "temperature": self.temperature,
            "tool_ids": self.compute_agent_tools(),
            "built_in_tools": self._compute_built_in_tools(),
            "knowledge_base": (
                [
                    {
                        "type": "text",
                        "name": self.knowledge_base_name,
                        "id": self.knowledge_base_id,
                    }
                ]
                if self.knowledge_base_note
                else []
            ),
        }

        if self.max_tokens > 0:
            prompt_dict["max_tokens"] = self.max_tokens

        # Add custom LLM configuration if using custom-llm
        if self.llm == "custom-llm":
            custom_llm_config = self._build_custom_llm_config()
            if custom_llm_config:
                prompt_dict["custom_llm"] = custom_llm_config

        return prompt_dict

    def _build_custom_llm_config(self):
        """Build custom LLM configuration for ElevenLabs agents.

        This is a stub method that returns None by default.
        Install the connect_elevenlabs_openai module to enable custom LLM
        support via OpenAI-compatible endpoints.

        Returns:
            dict or None: Custom LLM configuration, or None if not configured.
        """
        return None

    def _build_platform_settings(self) -> AgentPlatformSettingsRequestModel:
        """Build AgentPlatformSettingsRequestModel using SDK types."""
        return AgentPlatformSettingsRequestModel(
            archived=self.elevenlabs_archived,
            overrides={
                "conversation_config_override": {
                    "agent": {
                        "language": True,
                    }
                },
            },
            call_limits={
                "agent_concurrency_limit": self.agent_concurrency_limit,
                "daily_limit": self.daily_limit,
            },
        )

    def compute_platform_settings(self):
        return {
            "archived": self.elevenlabs_archived,
            "overrides": {
                "conversation_config_override": {
                    "agent": {
                        "language": True,
                    }
                },
            },
            "call_limits": {
                "agent_concurrency_limit": self.agent_concurrency_limit,
                "daily_limit": self.daily_limit,
            },
        }

    def compute_language_presets(self):
        res = {}
        first_message_translations = self.get_field_translations(
            "first_message"
        )[0]
        for trans in first_message_translations:
            if (
                trans["lang"]
                not in self.additional_languages.mapped("code")
            ):
                logger.info(
                    "Not using language %s because not included in additional_languages.",
                    trans["lang"],
                )
                continue
            res[trans["lang"].split("_")[0]] = {
                "overrides": {
                    "agent": {
                        "first_message": trans["value"],
                    }
                }
            }
        return res

    def compute_agent_tools(self):
        agent_tools = []
        for tool in self.tools:
            if tool.tool_id:
                agent_tools.append(tool.tool_id)
        return agent_tools

    def print_config(self):
        client = self.env["connect.settings"].get_elevenlabs_client()
        agents = client.conversational_ai.agents.list().agents
        for agent in agents:
            agent = client.conversational_ai.agents.get(
                agent_id=agent.agent_id
            )
            print(
                json.dumps(
                    str(agent.conversation_config.agent), indent=2
                )
            )
