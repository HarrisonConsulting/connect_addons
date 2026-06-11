# -*- coding: utf-8 -*-
import inspect
import json
import logging
from multiprocessing import RLock
import os
import secrets

import httpx
import openai
import phonenumbers
import requests
import random
import re
import string
from urllib.parse import urljoin, urlsplit, urlunsplit
import uuid
from odoo import fields, models, api, release
from odoo.exceptions import ValidationError, UserError
from odoo.tools import config
from twilio.rest import Client
from twilio.http.http_client import TwilioHttpClient
from .audio_referrer_mixin import (
    SELECTABLE_AUDIO_STATES,
    URL_PLAYABLE_AUDIO_SOURCES,
)

logger = logging.getLogger(__name__)

TWILIO_LOG_LEVEL = logging.WARNING

# Dev/test credential override. Process environment (or a gitignored .env at
# the repo root) supplies CONNECT_* values that take precedence over the
# credentials stored in connect.settings. Environment wins deliberately: a
# database cloned from production carries live carrier credentials, and the
# override is what guarantees a dev session talks to the sandbox account
# instead. Production is unaffected — no .env is ever committed (gitignored)
# and no CONNECT_* variables exist there.
ENV_OVERRIDE_PARAMS = {
    'account_sid': 'CONNECT_ACCOUNT_SID',
    'auth_token': 'CONNECT_AUTH_TOKEN',
    'twilio_api_key': 'CONNECT_TWILIO_API_KEY',
    'twilio_api_secret': 'CONNECT_TWILIO_API_SECRET',
    'twilio_region': 'CONNECT_TWILIO_REGION',
    'twilio_edge': 'CONNECT_TWILIO_EDGE',
}

DOTENV_PATH = os.path.join(os.path.dirname(__file__), '..', '..', '.env')

_dotenv_cache = {'mtime': None, 'values': {}}


def _dotenv_values():
    """Parse the repo-root .env (KEY=VALUE lines), cached on file mtime."""
    try:
        mtime = os.stat(DOTENV_PATH).st_mtime
    except OSError:
        return {}
    if _dotenv_cache['mtime'] != mtime:
        values = {}
        try:
            with open(DOTENV_PATH) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#') or '=' not in line:
                        continue
                    key, _, value = line.partition('=')
                    values[key.strip()] = value.strip().strip('"\'')
        except OSError:
            return {}
        _dotenv_cache.update(mtime=mtime, values=values)
    return _dotenv_cache['values']


def get_env_credential(param):
    """Return the CONNECT_* override for a credential param, or None.

    Stands down under test mode (``--test-enable``): tests assert DB
    round-trips and unconfigured-credential behavior, and must not be
    shadowed by whatever .env happens to sit in the checkout.
    """
    if config['test_enable']:
        return None
    env_name = ENV_OVERRIDE_PARAMS.get(param)
    if not env_name:
        return None
    return os.environ.get(env_name) or _dotenv_values().get(env_name) or None

# HTTP request timeouts: (connect_timeout_secs, read_timeout_secs)
HTTP_DOWNLOAD_TIMEOUT = (10, 60)   # For media downloads (audio files)
HTTP_API_TIMEOUT = (10, 30)        # For API calls (JSON responses)

############### SETTINGS #####################################
MODULE_NAME = "connect"
MAX_EXTEN_LEN = 4
PROTECTED_FIELDS = [
    "display_auth_token",
    "display_region_auth_token",
    "display_twilio_api_secret",
    "display_openai_api_key",
]

TWILIO_EDGES = [
    ('ashburn', 'US East Coast (Virginia)'),
    ('umatilla', 'US West Coast (Oregon)'),
    ('dublin', 'Ireland'),
    ('frankfurt', 'Frankfurt'),
    ('sydney', 'Australia'),
    ('sao-paulo', 'Brazil'),
    ('tokyo', 'Japan'),
    ('singapore', 'Singapore'),
]


class _RewriteHostHttpClient(TwilioHttpClient):
    """Twilio HTTP client that rewrites the request host.

    Lets Connect point the Twilio SDK at a Twilio-API-compatible provider
    (e.g. VoiceTel's ``voiceml.voicetel.com``) without touching the hundreds of
    SDK call sites. The SDK builds canonical Twilio URLs (``api.twilio.com``,
    ``messaging.twilio.com`` ...); we swap the netloc on the way out. The
    provider's compatibility guarantee is that request/response shapes and the
    ``/2010-04-01/Accounts/...`` path layout match Twilio exactly.

    Scope: this only covers traffic routed through the Twilio SDK Client. Direct
    ``requests`` calls to Twilio (WhatsApp ``messaging``/``content`` endpoints,
    twimlets hold music) bypass it and remain Twilio-only.
    """

    def __init__(self, rewrite_host, **kwargs):
        super().__init__(**kwargs)
        self._rewrite_host = rewrite_host

    def request(self, method, url, params=None, data=None, headers=None,
                auth=None, timeout=None, allow_redirects=False):
        url = urlunsplit(urlsplit(url)._replace(netloc=self._rewrite_host))
        return super().request(method, url, params, data, headers, auth,
                               timeout, allow_redirects)


def debug(rec, message, level="info"):
    caller_module = inspect.stack()[1][3]
    if level == "info":
        fun = logger.info
    elif level == "warning":
        fun = logger.warning
        fun("++++++ {}: {}".format(caller_module, message))
    elif level == "error":
        fun = logger.error
        fun("++++++ {}: {}".format(caller_module, message))
    if rec.env["connect.settings"].sudo().get_param("debug_mode"):
        rec.env["connect.debug"].sudo().create(
            {
                "model": str(rec),
                "message": caller_module + ": " + message,
            }
        )
        if level == "info":
            fun("++++++ {}: {}".format(caller_module, message))


def format_connect_response(text):
    if not isinstance(text, str):
        text = str(text)
    symbol_pattern = re.compile(r"(\x08.)|\x08")
    text = symbol_pattern.sub("", text)
    color_pattern = re.compile(r"\x1b\[[\d;]+m")
    text = color_pattern.sub("", text)
    return text


def generate_password():
    characters = [
        random.choice(string.ascii_lowercase),
        random.choice(string.ascii_uppercase),
        random.choice(string.digits),
    ]
    characters += random.choices(string.ascii_letters + string.digits, k=20)
    random.shuffle(characters)
    return "".join(characters)


######### COPY FROM SETTINGS TO ELIMINATE CIRULAR IMPORT
def strip_number(number):
    """Strip number formating"""
    if not isinstance(number, str):
        return number
    pattern = r"[\s\(\)\-\+]"
    return re.sub(pattern, "", number).lstrip("0")


class Settings(models.Model):
    """One record model to keep all settings. The record is created on
    get_param / set_param methods on 1-st call.
    """

    _name = "connect.settings"
    _inherit = ['connect.audio.referrer.mixin']
    _description = "Settings"

    _audio_reference_fields = ('park_hold_music_audio_id',)

    name = fields.Char(compute="_get_name")
    debug_mode = fields.Boolean()
    # Stamped by connect.audio._refresh_reachability() on each full BFS pass.
    # Drives the "reachability last refreshed N ago" badge in the Audio
    # Overview — operators can tell at a glance whether is_reachable flags are
    # fresh or something's stopped triggering recomputes.
    last_reachability_refresh_on = fields.Datetime(readonly=True,
        string='Reachability Last Refreshed')

    def get_default_audio_source(self):
        """Return (source, voice) tuple for newly-created connect.audio rows.

        Override in provider extensions (e.g. connect_elevenlabs) to switch the
        default to that provider when enabled. Base default is twilio_tts with
        no explicit voice (caller falls back to play_on()'s default).
        """
        return 'twilio_tts', self.env['connect.voice']
    twilio_auto_sync = fields.Boolean(default=True)
    twilio_region = fields.Selection([
        ('us1', 'US East (Virginia)'),
        ('ie1', 'Ireland (Dublin)'),
        ('au1', 'Australia (Sydney)'),
    ], default='us1', required=True)
    twilio_edge = fields.Selection(selection=TWILIO_EDGES, required=True, default='ashburn')
    rest_api_host = fields.Char(
        string="REST API Host",
        help="Hostname of a Twilio-API-compatible provider (e.g. "
             "voiceml.voicetel.com). Leave empty to use Twilio. When set, all "
             "Twilio SDK REST traffic is routed to this host and the "
             "Region/Edge settings are ignored. Put the provider's Account SID "
             "in Account SID and its API key in Auth Token. Note: WhatsApp "
             "(messaging/content.twilio.com) is Twilio-only and is not affected "
             "by this setting.",
    )
    account_sid = fields.Char(string="Account SID")
    auth_token = fields.Char(
        groups="base.group_erp_manager,connect.group_connect_webhook"
    )
    display_auth_token = fields.Char()
    region_auth_token = fields.Char(
        groups="base.group_erp_manager,connect.group_connect_webhook"
    )
    display_region_auth_token = fields.Char()
    twilio_api_key = fields.Char()
    twilio_api_secret = fields.Char(groups="base.group_erp_manager")
    display_twilio_api_secret = fields.Char()
    twilio_balance = fields.Char(readonly=True)
    openai_api_key = fields.Char(groups="base.group_erp_manager")
    display_openai_api_key = fields.Char()
    openai_base_url = fields.Char(
        string='OpenAI Base URL',
        help='Custom base URL for OpenAI-compatible API (e.g., LiteLLM proxy). Leave empty for default OpenAI API.'
    )
    number_search_operation = fields.Selection(
        [("=", "Equal"), ("like", "Like")], default="=", required=True
    )
    ############# RECORDING & TRANSCRIPT FIELDS ##############################################
    proxy_recordings = fields.Boolean(
        help="Re-stream recordings using Odoo user auth.", default=True
    )
    transcript_calls = fields.Boolean()
    transcript_provider = fields.Selection(selection=[('openai', 'Open AI')], default='openai', required=True)
    summary_prompt = fields.Text(
        required=True,
        default=(
            "{number_name} is {number_description}.\n"
            "Your task is to produce a comprehensive summary of the {direction} call "
            "from {caller_name} ({caller_number}) to {called_name} ({called_number}) "
            "with the following transcription:\n"
            "```\n{transcript}\n```"
        ),
        help="Supports placeholders: {number_name}, {number_description}, "
             "{caller_name}, {called_name}, {caller_number}, {called_number}, "
             "{direction}, {transcript}. If {transcript} is included, it will be "
             "embedded in the prompt; otherwise the transcript is sent separately."
    )
    register_summary = fields.Boolean(
        default=True, help="Register summary at partner of reference chat."
    )
    fetch_call_prices = fields.Boolean(
        default=False,
        string="Fetch Call Prices",
        help="Enable fetching call prices from Twilio API after call completion. May add delay to call processing."
    )
    recording_storage = fields.Selection([
        ('twilio', 'Twilio (default)'),
        ('odoo_filestore', 'Odoo Filestore'),
    ], default='twilio', required=True, string='Recording Storage',
       help='Where to store call recordings and voicemails. Odoo Filestore downloads and stores audio locally.')
    delete_twilio_recording = fields.Boolean(
        default=False, string='Delete from Twilio After Transfer',
        help='Delete recordings from Twilio after successfully storing locally. Reduces Twilio storage costs.'
    )
    ############################################################
    instance_uid = fields.Char("Instance UID", compute="_get_instance_data")
    api_url = fields.Char("API URL", compute="_get_instance_data")
    api_fallback_url = fields.Char("API Fallback URL")
    twilio_verify_requests = fields.Boolean(
        default=True, string="Verify Twilio Requests"
    )
    # Registration fields
    customer_code = fields.Char()
    registration_number = fields.Char(compute="_get_instance_data")
    registration_key = fields.Char("API Key", compute="_get_instance_data")
    is_registered = fields.Boolean()
    i_agree_to_register = fields.Boolean()
    i_agree_to_contact = fields.Boolean()
    i_agree_to_receive = fields.Boolean()
    installation_date = fields.Datetime(compute="_get_instance_data")
    module_version = fields.Char(compute="_get_instance_data")
    odoo_version = fields.Char(compute="_get_instance_data")
    admin_name = fields.Char()
    admin_phone = fields.Char(
        help='It is required to contact this instance\u2019s administrator in case any critical vulnerabilities are found in the application.')
    admin_email = fields.Char(
        help='It is required to contact this instance administrator by email in case any non-critical vulnerabilities are found in the application.')
    company_name = fields.Char(help='Company name of this instance.')
    company_country = fields.Many2one('res.country',
                                      help='We use the company\u2019s country information for statistical tracking of our product installations by country.')
    web_base_url = fields.Char(compute="_get_instance_data", string="Odoo URL")
    call_duration_limit = fields.Integer(compute="_get_instance_data", string="Call Duration Limit (seconds)")
    latest_versions = fields.Html(readonly=True)
    # Voice settings. default_twilio_voice is the DB-wide default for any
    # connect.audio with source=twilio_tts and use_default_voice=True. Kept as
    # a Many2one on connect.voice so the set of valid voices is data-driven
    # (see connect/data/audio.xml) rather than hardcoded in a Selection.
    default_twilio_voice = fields.Many2one(
        'connect.voice', string='Default Twilio Voice',
        domain=[('provider', '=', 'twilio'), ('active', '=', True)],
        help='Default voice for Twilio <Say> output. Used by connect.audio '
             'rows with use_default_voice=True and by tts_mixin fallback '
             'system messages.')
    pronunciation_rules = fields.Text(
        string='Pronunciation Rules',
        help='JSON map of text to pronunciation substitutions (e.g., {"3CHI": "3-chee", "CEO": "C-E-O"})'
    )
    # Voicemail settings
    voicemail_max_length = fields.Integer(
        string='Voicemail Max Length',
        default=120,
        help="Maximum voicemail recording length in seconds"
    )
    voicemail_finish_key = fields.Selection(
        [('0', '0'), ('1', '1'), ('2', '2'), ('3', '3'), ('4', '4'),
         ('5', '5'), ('6', '6'), ('7', '7'), ('8', '8'), ('9', '9'),
         ('*', '*'), ('#', '#')],
        string='Voicemail Finish Key',
        default='#',
        help="Key that callers press to finish recording a voicemail"
    )
    # Park slot settings
    park_slot_count = fields.Integer(
        string='Park Slot Count',
        default=9,
        help="Number of available call parking slots (1-99)"
    )
    park_timeout = fields.Integer(
        string='Park Timeout',
        default=300,
        help="Seconds before a parked call times out and rings back the parker (0 = no timeout)"
    )
    park_hold_music_audio_id = fields.Many2one(
        'connect.audio', ondelete='set null',
        domain=[
            ('state', 'in', SELECTABLE_AUDIO_STATES),
            ('source', 'in', URL_PLAYABLE_AUDIO_SOURCES),
        ],
        string='Park Hold Music',
        help="Audio played to parked callers. Leave empty for default classical "
             "music. Twilio's waitUrl only accepts a media URL, so TTS sources "
             "can't be used here — pick a Browser Recording, Internal "
             "Attachment, or External URL."
    )
    # Related surfacing of the picked audio's source so the settings form can
    # show a warning banner when an operator picks a TTS audio (which can't
    # play via waitUrl and will silently fall back to the default loop).
    park_hold_music_audio_id_source = fields.Selection(
        related='park_hold_music_audio_id.source', readonly=True)
    park_announcement_enabled = fields.Boolean(
        string='Park Announcement',
        default=False,
        help="Play slot number announcement when parking a call"
    )
    # Messaging settings
    enable_whatsapp = fields.Boolean(
        default=True,
        string='Enable WhatsApp',
        help='Show WhatsApp menus and action buttons. Disable for deployments that do not use WhatsApp.'
    )

    # Dialing defaults
    default_country_code = fields.Char(
        string='Default Country Code',
        help='ISO country code (e.g. US, GB, DE) to assume when dialing numbers without a country code. '
             'Numbers dialed without a + prefix will be interpreted as belonging to this country.'
    )

    def get_module_version(self, module_name):
        module = (
            self.env["ir.module.module"].sudo().search([("name", "=", module_name)])
        )
        module_version = (
            re.sub(r"^(\d+\.\d+\.)", "", module.installed_version) if module else ""
        )
        return module_version

    @staticmethod
    def get_module_list():
        return ["connect"]

    def check_latest_versions(self):
        module_list = self.get_module_list()
        request_data = {
            "instance_uid": self.get_param("instance_uid"),
            "odoo_version": release.major_version,
            "module_list": module_list,
        }
        response = self.make_usage_request(
            "check_versions", requests.post, data=request_data, raise_on_error=True
        )
        data = []
        for module in module_list:
            current_version = self.get_module_version(module)
            latest_version = response.get(module, "")
            data.append(
                {
                    "name": module,
                    "current_version": current_version,
                    "latest_version": latest_version,
                }
            )

        html = self.env["ir.ui.view"]._render_template(
            "connect.module_version_template", {"data": data}
        )
        self.set_param("latest_versions", html)

    def set_default_admin_and_company(self):
        self.company_name = self.env.user.company_id.name
        self.company_country = self.env.user.company_id.country_id
        self.admin_name = self.env.user.partner_id.name
        self.admin_email = self.env.user.partner_id.email
        self.admin_phone = self.env.user.partner_id.phone

    def read(self, fields_to_read, load='_classic_read'):
        if not self.admin_name:
            self.set_default_admin_and_company()
        res = super(Settings, self).read(fields_to_read, load=load)
        return res

    def _get_instance_data(self):
        module = (
            self.env["ir.module.module"].sudo().search([("name", "=", MODULE_NAME)])
        )
        for rec in self:
            rec.module_version = re.sub(r"^(\d+\.\d+\.)", "", module.installed_version)
            rec.odoo_version = release.major_version
            rec.instance_uid = (
                self.env["ir.config_parameter"].sudo().get_param("connect.instance_uid")
            )
            # Format API URL according to the preferred region or dev URL.
            rec.installation_date = (
                self.env["ir.config_parameter"]
                .sudo()
                .get_param("connect.installation_date")
            )
            rec.api_url = (
                self.env["ir.config_parameter"].sudo().get_param("connect.api_url")
            )
            rec.registration_key = (
                self.env["ir.config_parameter"]
                .sudo()
                .get_param("connect.registration_key")
            )
            rec.web_base_url = (
                self.env["ir.config_parameter"].sudo().get_param("web.base.url")
            )
            rec.registration_number = (
                self.env["ir.config_parameter"]
                .sudo()
                .get_param("connect.registration_number")
            )
            rec.call_duration_limit = int(
                self.env["ir.config_parameter"]
                .sudo()
                .get_param("connect.call_duration_limit", "7200")
            )

    @api.model
    def connect_notify(
        self, message, title="Connect", notify_uid=None, sticky=False, warning=False
    ):
        """Send a notification to logged in Odoo user.

        Args:
            message (str): Notification message.
            title (str): Notification title. If not specified: PBX.
            uid (int): Odoo user UID to send notification to. If not specified: caller user UID.
            sticky (boolean): Make a notiication message sticky (shown until closed). Default: False.
            warning (boolean): Make a warning notification type. Default: False.
        Returns:
            Always True.
        """
        # Use calling user UID if not specified.
        if not notify_uid:
            notify_uid = self.env.uid

        self.env["bus.bus"]._sendone(
            "connect_actions_{}".format(notify_uid),
            "connect_notify",
            {
                "message": message,
                "title": title,
                "sticky": sticky,
                "warning": warning,
            },
        )

        return True

    @api.model
    def connect_reload_view(self, model):
        msg = {"model": model}
        self.env["bus.bus"]._sendone("connect_actions", "reload_view", msg)

    @api.model
    def set_defaults(self):
        # Called on installation to set default value
        api_url = self.get_param("api_url")
        if not api_url:
            # Set default value
            web_base_url = (
                self.env["ir.config_parameter"].sudo().get_param("web.base.url")
            )
            self.env["ir.config_parameter"].set_param("connect.api_url", web_base_url)
        installation_date = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("connect.installation_date")
        )
        if not installation_date:
            installation_date = fields.Datetime.now()
            self.env["ir.config_parameter"].set_param(
                "connect.installation_date", installation_date
            )
            user = self.env.ref("connect.user_connect_webhook")
            chars = string.ascii_letters + string.digits + string.punctuation
            password = 'X1!x' + ''.join(secrets.choice(chars) for _ in range(16))
            user.write({'password': password})

    @api.model
    def _get_name(self):
        for rec in self:
            rec.name = "General Settings"

    def open_settings_form(self):
        rec = self.search([])
        if not rec:
            rec = self.sudo().with_context(no_constrains=True).create({})
        else:
            rec = rec[0]
        return {
            "type": "ir.actions.act_window",
            "res_model": "connect.settings",
            "res_id": rec.id,
            "name": "General Settings",
            "view_mode": "form",
            "view_id": self.env.ref("connect.connect_settings_form").id,
            "target": "current",
        }

    def action_sync_park_slots(self):
        """Button action to sync park slots with current park_slot_count setting."""
        self.env['connect.park_slot'].sync_slots()

    @api.model
    # @ormcache('param')
    def get_param(self, param, default=False):
        """ """
        override = get_env_credential(param)
        if override is not None:
            return override
        data = self.search([])
        if not data:
            data = self.sudo().with_context(no_constrains=True).create({})
        else:
            data = data[0]
        return getattr(data, param, default)

    @api.model
    def set_param(self, param, value):
        data = self.search([])
        if not data:
            data = self.sudo().with_context(no_constrains=True).create({})
        else:
            data = data[0]
        setattr(data, param, value)

    @api.model
    def set_instance_uid(self, instance_uid=False):
        existing_uid = self.env["ir.config_parameter"].get_param("connect.instance_uid")
        if not existing_uid:
            if not instance_uid:
                instance_uid = str(uuid.uuid4())
            self.env["ir.config_parameter"].set_param(
                "connect.instance_uid", instance_uid
            )

    def register_instance(self):
        if not self.env.user.has_group("base.group_system"):
            raise ValidationError("Only Odoo admin can do it!")
        if self.get_param("is_registered"):
            raise ValidationError("This instance is already registered!")
        data = self.prepare_registration_data()
        if not data.get("customer_code"):
            raise ValidationError("Enter your customer code!")
        required_fields = [
            "admin_email",
            "admin_name",
            "admin_phone",
            "company_name",
            "company_country",
            "installation_date",
            "module_name",
            "module_version",
            "url",
            "odoo_version",
        ]
        missing_fields = [field for field in required_fields if not data.get(field)]
        if missing_fields:
            raise ValidationError(
                f"Please fill in the following fields: {', '.join([k.replace('_', ' ').capitalize() for k in missing_fields])}"
            )
        res = self.make_usage_request(
            "registration", requests.post, data=data, raise_on_error=True
        )
        self.env["ir.config_parameter"].sudo().set_param(
            "connect.registration_key", res.get("registration_key")
        )
        self.env["ir.config_parameter"].sudo().set_param(
            "connect.registration_number", res.get("registration_number")
        )
        self.set_param("is_registered", True)
        self.connect_notify("Instance registered successfully!", title="Registration")

    def update_instance_registration(self):
        if not self.env.user.has_group("base.group_system"):
            raise ValidationError("Only Odoo admin can do it!")
        if not self.get_param("is_registered"):
            raise ValidationError("This instance is not registered yet! Please register first.")
        data = self.prepare_registration_data()
        required_fields = [
            "admin_email",
            "admin_name",
            "admin_phone",
            "company_name",
            "company_country",
            "installation_date",
            "module_name",
            "module_version",
            "url",
            "odoo_version",
        ]
        missing_fields = [field for field in required_fields if not data.get(field)]
        if missing_fields:
            raise ValidationError(
                f"Please fill in the following fields: {', '.join([k.replace('_', ' ').capitalize() for k in missing_fields])}"
            )
        res = self.make_usage_request(
            "update_registration", requests.post, data=data, raise_on_error=True
        )
        # Display the message returned from the API
        message = res.get("message", "Registration updated successfully!")
        self.connect_notify(message, title="Registration Update")

    def prepare_registration_data(self):
        company_country = self.get_param("company_country")
        return {
            "instance_uid": self.get_param("instance_uid"),
            "company_name": self.get_param("company_name"),
            "company_country": company_country.name if company_country else False,
            "company_country_code": company_country.code if company_country else False,
            "company_country_name": company_country.name if company_country else False,
            "admin_name": self.get_param("admin_name"),
            "admin_email": self.get_param("admin_email"),
            "admin_phone": self.get_param("admin_phone"),
            "module_version": self.get_param("module_version"),
            "module_name": MODULE_NAME,
            "odoo_version": self.get_param("odoo_version"),
            "odoo_full_version": release.version,
            "url": self.get_param("web_base_url"),
            "installation_date": self.get_param("installation_date").strftime(
                "%Y-%m-%d"
            ),
            "customer_code": self.get_param("customer_code"),
        }

    def get_usage_model_list(self):
        return [
            "call",
            "callflow",
            "domain",
            "exten",
            "message",
            "number",
            "outgoing_callerid",
            "recording",
            "twiml",
            "user",
        ]

    @api.model
    def update_usage(self):
        res = {
            "usage": {},
            "usage_errors": {},
        }
        for model in self.get_usage_model_list():
            try:
                res["usage"][model] = {
                    "count": self.env["connect.{}".format(model)].search_count([]),
                }
                if model == "call":
                    self.env.cr.execute("SELECT SUM(duration)/60 FROM connect_call")
                    call_minutes = self.env.cr.fetchall()[0][0]
                    res["usage"][model]["minutes"] = call_minutes
            except Exception as e:
                res["errors"][model] = str(e)
        data = self.prepare_registration_data()
        data.update(res)
        try:
            self.make_usage_request("usage", requests.post, data)
        except Exception as e:
            logger.exception("Usage error:")

    def make_usage_request(
        self, path, method, data={}, headers={}, raise_on_error=False
    ):
        url = self.env["ir.config_parameter"].get_param(
            "connect.registration_url", "https://api1.oduist.com/instance/"
        )
        if not url.endswith("/"):
            url = "{}/".format(url)
        res = None
        try:
            res = method(urljoin(url, path), json=data, headers=headers)
            if res.status_code == 200:
                res = res.json()
                if res.get("error"):
                    raise ValidationError(res["error"])
                return res
            else:
                raise ValidationError(res.text)
        except Exception as e:
            if raise_on_error:
                raise ValidationError(str(e))
            else:
                return {}

    @api.model_create_multi
    def create(self, vals_list):
        self.env.registry.clear_cache()
        return super(Settings, self).create(vals_list)

    def write(self, vals):
        if self.env.context.get("skip_protected_fields"):
            return super(Settings, self).write(vals)
        if not self.openai_api_key and vals.get("display_openai_api_key"):
            vals.update({"transcript_calls": True})
        res = super(Settings, self).write(vals)
        if 'enable_whatsapp' in vals:
            whatsapp_menus = [
                'connect.connect_whatsapp_sender_menu',
                'connect.connect_message_content_template_menu',
            ]
            for xml_id in whatsapp_menus:
                menu = self.env.ref(xml_id, raise_if_not_found=False)
                if menu:
                    menu.sudo().write({'active': bool(vals['enable_whatsapp'])})
        changed_fields = {}
        for field_name in PROTECTED_FIELDS:
            if vals.get(field_name):
                value = vals[field_name]
                # Never overwrite real credentials with masked asterisk values
                if value == '*' * len(value):
                    continue
                changed_fields.update(
                    {
                        field_name.replace("display_", ""): value,
                        field_name: "*" * len(value),
                    }
                )
        if changed_fields:
            # Set keys user super access.
            self.with_context(skip_protected_fields=True).sudo().write(changed_fields)
        # Reset cache
        self.env.registry.clear_cache()

    @api.model
    def get_system_voice(self):
        """Return the Twilio voice external_id for <Say> fallbacks.

        Resolves settings.default_twilio_voice → external_id. Falls back to
        DEFAULT_TWILIO_VOICE (Polly.Joanna Standard) when unset so <Say>
        always has a concrete voice to render even on a fresh install before
        the operator picks one.
        """
        from .tts_mixin import DEFAULT_TWILIO_VOICE
        voice = self.sudo().search([], limit=1).default_twilio_voice
        return voice.external_id if voice else DEFAULT_TWILIO_VOICE

    @api.model
    def process_pronunciation(self, text):
        """Process text to apply SSML pronunciation substitutions"""
        if not text:
            return text

        try:
            rules_json = self.sudo().get_param('pronunciation_rules')
            if not rules_json:
                return text

            rules = json.loads(rules_json)
            processed_text = text
            has_substitutions = False

            for original, pronunciation in rules.items():
                pattern = re.compile(re.escape(original), re.IGNORECASE)
                if pattern.search(processed_text):
                    # SSML-escape the pronunciation value — a quote or angle
                    # bracket in the operator-edited rules JSON would break
                    # the <sub> tag and cause Twilio to speak the raw text.
                    safe = (pronunciation
                            .replace('&', '&amp;')
                            .replace('"', '&quot;')
                            .replace('<', '&lt;')
                            .replace('>', '&gt;'))
                    def replace_func(match, alias=safe):
                        return f'<sub alias="{alias}">{match.group(0)}</sub>'

                    processed_text = pattern.sub(replace_func, processed_text)
                    has_substitutions = True

            if has_substitutions:
                processed_text = f'<speak>{processed_text}</speak>'

            return processed_text

        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f'Error processing pronunciation rules: {e}')
            return text

    @api.model
    def get_client(self, region=True):
        try:
            self.check_access("read")
            account_sid = self.sudo().get_param("account_sid")
            auth_token = self.sudo().get_param("auth_token")
            if not account_sid or not auth_token:
                logger.warning("Twilio credentials not configured (account_sid=%s, auth_token=%s)",
                               bool(account_sid), bool(auth_token))
                return None
            token_to_use = auth_token
            if region:
                region_auth_token = self.sudo().get_param("region_auth_token")
                token_to_use = region_auth_token if region_auth_token else auth_token
            # A custom REST host points the SDK at a Twilio-compatible provider
            # (e.g. VoiceTel). The host is then fixed, so Twilio region/edge
            # routing is moot and intentionally skipped.
            rest_api_host = (self.sudo().get_param("rest_api_host") or "").strip()
            http_client = _RewriteHostHttpClient(rest_api_host) if rest_api_host else None
            client = Client(account_sid, token_to_use, http_client=http_client)
            if region and not rest_api_host:
                twilio_region = self.sudo().get_param("twilio_region")
                if twilio_region:
                    client.region = twilio_region
                twilio_edge = self.sudo().get_param("twilio_edge")
                if twilio_edge:
                    client.edge = twilio_edge
            client.http_client.logger.setLevel(TWILIO_LOG_LEVEL)
            return client
        except Exception as e:
            if "Credentials are required to create a TwilioClient" in str(e):
                raise ValidationError("Set Twilio API keys first!")
            else:
                raise

    @api.model
    def get_openai_client(self):
        api_key = self.sudo().get_param('openai_api_key')
        if not api_key:
            return False
        base_url = self.sudo().get_param('openai_base_url')
        # Build kwargs for OpenAI client
        kwargs = {'api_key': api_key}
        if base_url:
            # Normalize base_url to ensure it ends with /v1 for OpenAI compatibility
            base_url = base_url.rstrip('/')
            if not base_url.endswith('/v1'):
                base_url = base_url + '/v1'
            kwargs['base_url'] = base_url
        if os.environ.get('OPENAI_PROXY'):
            kwargs['http_client'] = httpx.Client(proxy=os.environ.get('HTTPS_PROXY'))
        client = openai.OpenAI(**kwargs)
        return client

    def check_api_url(self):
        message = None
        if re.match(r"^http://", self.get_param("api_url")):
            message = "Invalid api url! Please use HTTPS instead of HTTP to ensure a secure connection!"
        if re.match(
            r"(http|https)://(localhost|127\.0\.0\.\d)(:\d+)?",
            self.get_param("api_url"),
        ):
            message = "Invalid api url! Localhost is not allowed! Please use a valid and secure domain!"
        if message:
            logger.warning(message)
        return message

    def sync(self):
        if not (
            self.sudo().get_param("account_sid") and self.sudo().get_param("auth_token")
        ):
            raise ValidationError("You must set account SID and Auth token!")
        api_url_check = self.check_api_url()
        if api_url_check:
            raise ValidationError(api_url_check)
        try:
            self.env["connect.twiml"].sync()
            self.env["connect.domain"].sync()
            self.env["connect.number"].sync()
            self.env["connect.outgoing_callerid"].sync()
            self.env["connect.whatsapp_sender"].sync()
            self.env["connect.message_content_template"].sync()
        except Exception as e:
            if 'errors/20003' in str(e):
                raise ValidationError('Error authenticating requests to the Twilio API! Check your Auth Key!')
            else:
                raise

    # Called from the settings.
    def reformat_numbers_button(self):
        for rec in self.env["res.partner"].search([]):
            rec.phone = rec._normalize_phone(rec.phone)
            rec.mobile = rec._normalize_phone(rec.mobile)

    def compute_sip_uri(self, user):
        return "sip:{}".format(self.env.user.connect_user.uri)

    def get_external_call_route(self, number, callerId, status_url,
            record='do-not-record', record_status_url=None):
        call_duration_limit = int(self.sudo().get_param('call_duration_limit'))
        twiml = """
        <Response>
            <Dial record="{}" recordingStatusCallback="{}" callerId="{}" timeLimit="{}"><Number statusCallback='{}' statusCallbackEvent='initiated answered completed'>{}</Number></Dial>
        </Response>
        """.format(
            record, record_status_url, callerId, call_duration_limit, status_url, number
        )
        return twiml

    @api.model
    def originate_call(self, number, res_model=None, res_id=None, user=None, whatsapp_call=False):
        number = strip_number(number)
        if len(number) > MAX_EXTEN_LEN:
            default_country = self.sudo().get_param('default_country_code') or None
            try:
                parsed = phonenumbers.parse(number, default_country)
                if phonenumbers.is_possible_number(parsed):
                    number = phonenumbers.format_number(
                        parsed, phonenumbers.PhoneNumberFormat.E164)
                else:
                    number = "+{}".format(number)
            except phonenumbers.NumberParseException:
                number = "+{}".format(number)
        client = self.get_client()
        partner_id = False
        obj = self.env[res_model].browse(res_id) if res_model and res_id else False
        caller_name = ""
        if res_model == "res.partner" and obj:
            partner_id = res_id
            caller_name = obj.display_name
        elif obj and hasattr(obj, "partner_id") and obj.partner_id:
            partner_id = obj.partner_id.id
            caller_name = obj.partner_id.display_name
        elif obj and hasattr(obj, "partner") and obj.partner:
            partner_id = obj.partner.id
            caller_name = obj.partner.display_name
        # If user is not set use current user.
        if not user:
            user = self.env.user
        if not user.connect_user:
            raise ValidationError("User does not have a SIP username defined!")
        # Get the first ring channel for the user
        first_flow = self.env['connect.user_callflow'].search([
            ('user', '=', user.id), ('callflow_type', 'in', ['client', 'sip'])], order='prio', limit=1)
        if first_flow.callflow_type == 'sip':
            to = self.compute_sip_uri(user)
        else:
            to = (
                "client:{}?autoAnswer=yes&Partner={}&CallerName={}".format(
                    self.env.user.connect_user.uri, partner_id or '', caller_name or ''
                )
            )
        if "client:" in to:
            # Strip + before sending as param.
            to += "&From={}".format((number or '').replace("+", ""))
        exten = self.env["connect.exten"].search([("number", "=", number)], limit=1)
        api_url = self.sudo().get_param("api_url")
        edge = self.twilio_edge or self.env['connect.settings'].get_param('twilio_edge')
        status_url = urljoin(api_url, "twilio/webhook/callstatus#e={}".format(edge))
        record = 'record-from-answer-dual' if self.env.user.connect_user.record_calls else 'do-not-record'
        record_status_url = urljoin(api_url, "twilio/webhook/recordingstatus#e={}".format(edge))
        # Resolve callerId
        if exten:
            # Internal call to an extension.
            callerId = user.connect_user.exten.number
            twiml = exten.render()
        else:
            if whatsapp_call:
                # WhatsApp callerId selection akin to domain.originate_whatsapp_call
                pbx_user = user.connect_user
                sender = self.env['connect.whatsapp_sender'].get_default_sender(pbx_user)
                caller_number = sender.number if sender else False
                if not caller_number:
                    raise ValidationError("You must configure a WhatsApp sender!")
                callerId = f"whatsapp:{caller_number}"
                # Build WhatsApp Dial
                twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Dial callerId="{}" record="{}" recordingStatusCallback="{}">
        <WhatsApp statusCallback="{}" statusCallbackEvent="ringing answered completed">{}</WhatsApp>
    </Dial>
</Response>""".format(callerId, record, record_status_url, status_url, number)
            else:
                # Regular phone call
                default_number = self.env["connect.outgoing_callerid"].search(
                    [("is_default", "=", True)], limit=1
                )
                if user.connect_user.outgoing_callerid:
                    callerId = user.connect_user.outgoing_callerid.number
                else:
                    callerId = default_number.number
                twiml = self.get_external_call_route(
                    number, callerId, status_url, record=record, record_status_url=record_status_url)
        debug(self, 'Originate destination TwiML: {}'.format(twiml))
        channel = client.calls.create(
            twiml=twiml,
            to=to,
            from_=callerId,
            status_callback=status_url,
            status_callback_event=["initiated", "answered", "completed"],
        )
        self.env["connect.channel"].sudo().create(
            {
                "sid": channel.sid,
                "technical_direction": "outbound-api",
                "caller_user": user.id,
                "caller_pbx_user": user.connect_user.id,
                "partner": partner_id,
                "called": number,
                "caller": callerId,
            }
        )

    @api.onchange("transcript_calls")
    def _require_openai_key(self):
        if not self.sudo().get_param("openai_api_key"):
            raise ValidationError("You must set OpenAI key first!")

    def action_open_system_parameters(self):
        return {
            "type": "ir.actions.act_window",
            "name": "System Parameters",
            "res_model": "ir.config_parameter",
            "view_mode": "list,form",
            "target": "current",
            "context": {"search_default_key": "connect.api_url"},
        }

    @api.onchange('twilio_region')
    def _reset_twilio_edge(self):
        if self.twilio_region == 'us1':
            self.twilio_edge = 'ashburn'
        elif self.twilio_region == 'ie1':
            self.twilio_edge = 'dublin'
        elif self.twilio_region == 'au1':
            self.twilio_edge = 'sydney'

    def get_twilio_balance(self):
        """Fetch current Twilio account balance"""
        try:
            client = self.get_client()

            # Try to fetch balance using the balance resource
            try:
                balance_item = client.api.v2010.account.balance.fetch()
                currency = getattr(balance_item, 'currency', 'USD')
                balance_value = getattr(balance_item, 'balance', '0.00')
                balance = f"${balance_value} {currency}"
            except Exception as balance_error:
                # If balance API is not available (404 error), show informative message
                if '20404' in str(balance_error) or 'not found' in str(balance_error).lower():
                    balance = "Balance API not available for this account"
                    self.set_param('twilio_balance', balance)
                    self.connect_notify(f"Twilio Balance: {balance}. The balance endpoint may not be available for your account type or region.", title="Balance Info")
                    return balance
                else:
                    raise balance_error

            self.set_param('twilio_balance', balance)
            self.connect_notify(f"Twilio Balance: {balance}", title="Balance Update")
            return balance
        except Exception as e:
            error_msg = f"Failed to fetch Twilio balance: {str(e)}"
            self.connect_notify(error_msg, title="Balance Error", warning=True)
            raise ValidationError(error_msg)
