# -*- coding: utf-8 -*-
import inspect
import json
import logging
import os
import secrets

import httpx
import openai
import phonenumbers
import random
import re
import string
from urllib.parse import urljoin, urlsplit, urlunsplit
import uuid
from odoo import fields, models, api
from odoo.exceptions import ValidationError
from odoo.tools import config
from twilio.rest import Client
from twilio.http.http_client import TwilioHttpClient
from .audio_referrer_mixin import (
    SELECTABLE_AUDIO_STATES,
    URL_PLAYABLE_AUDIO_SOURCES,
)
from ..migrators import DomainMigrator, NumberMigrator, TwimlMigrator

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

DEFAULT_SIP_DOMAIN_SUFFIX = 'sip.twilio.com'
DEFAULT_HOLD_MUSIC_URL = (
    'http://twimlets.com/holdmusic?Bucket=com.twilio.music.classical'
)


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
    rest_provider = fields.Selection(
        [('twilio', 'Twilio')],
        default='twilio',
        required=True,
        string='Telephony Provider',
        help="REST API provider used for calls, numbers and SIP. Provider "
             "modules such as Connect VoiceTel add options here; Twilio is "
             "the built-in default.",
    )
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
    sip_domain_suffix = fields.Char(
        string="SIP Domain Suffix",
        help="Suffix for SIP domain hostnames (subdomain.suffix). Leave empty "
             "for Twilio (sip.twilio.com). Set to your registrar host when "
             "using a Twilio-compatible voice API with your own SIP edge.",
    )
    default_hold_music_url = fields.Char(
        string="Default Hold Music URL",
        help="Audio URL for conference hold and call park wait music. Leave "
             "empty to use the Twilio twimlets default.",
    )
    webrtc_provider = fields.Selection(
        [
            ('twilio', 'Twilio WebRTC (browser phone)'),
            ('disabled', 'Disabled (SIP phones only)'),
        ],
        default='twilio',
        required=True,
        string="Browser Phone",
        help="The in-Odoo softphone uses Twilio WebRTC and Twilio media edges. "
             "Disable when REST points at a compatible voice API without "
             "Twilio browser phone; use SIP desk phones instead.",
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
    customer_portal_calls_enabled = fields.Boolean(
        string='Customer Call Portal',
        default=False,
        help='Let portal customers view calls linked directly to their contact, '
             'including recordings, voicemails, and available transcripts.',
    )
    ############################################################
    instance_uid = fields.Char("Instance UID", compute="_get_instance_data")
    api_url = fields.Char("API URL", compute="_get_instance_data")
    api_fallback_url = fields.Char("API Fallback URL")
    twilio_verify_requests = fields.Boolean(
        default=True,
        string="Verify Twilio Requests",
        help='Validate every public Twilio callback using its request signature. '
             'Disabling this setting rejects all Twilio callbacks; it never '
             'permits unsigned requests.',
    )
    web_base_url = fields.Char(compute="_get_instance_data", string="Odoo URL")
    call_duration_limit = fields.Integer(compute="_get_instance_data", string="Call Duration Limit (seconds)")
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

    def _get_instance_data(self):
        parameters = self.env["ir.config_parameter"].sudo()
        for rec in self:
            rec.instance_uid = parameters.get_param("connect.instance_uid")
            rec.api_url = parameters.get_param("connect.api_url")
            rec.web_base_url = parameters.get_param("web.base.url")
            rec.call_duration_limit = int(
                parameters.get_param("connect.call_duration_limit", "7200")
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
    def _is_neutralized(self):
        """True once Odoo's standard neutralize.sql has run (dev clones,
        odoo.sh staging duplicates). Missing/disabled Twilio config on a
        neutralized database is expected, not an incident.
        """
        value = self.env['ir.config_parameter'].sudo().get_param('database.is_neutralized', False)
        return str(value).lower() in ('true', 't', '1')

    @api.model
    def check_security_preflight(self):
        """Runtime health check for the classic misconfigurations that leave
        Twilio webhook signature verification ineffective or disabled. Called
        from _register_hook, which runs on server start, install, and upgrade
        -- so drift (the toggle gets flipped off later, or credentials get
        cleared, well after install) is caught on the next restart too, with
        no cron worker needed. Returns a list of {code, level, message} dicts
        and never raises -- a failing check here must not block boot/install.
        Findings are ALSO logged (not just returned): a 'critical' log is what
        actually reaches an admin, since this workspace's error-capture
        pipeline picks up critical-level logs without needing any new UI here.
        """
        findings = []
        data = self.sudo().search([], limit=1)
        if not data:
            return findings  # nothing configured yet -- nothing to warn about

        account_sid = data.get_param('account_sid')
        auth_token = data.get_param('auth_token')
        activated = bool(account_sid or auth_token)
        if not activated:
            return findings

        # Not-production signals: a test run, a CONNECT_* sandbox override, OR the
        # standard Odoo neutralization flag (odoo/addons/base/data/neutralize.sql
        # sets ir_config_parameter['database.is_neutralized'] on every neutralize
        # run, including odoo.sh staging). Any of them means missing/disabled Twilio
        # verification is expected, not an incident. A test run has to be named
        # explicitly: get_env_credential stands down under test_enable, so relying on
        # it alone would report the most sandboxed context there is as production.
        sandboxed = (
            config['test_enable']
            or get_env_credential('account_sid') is not None
            or self._is_neutralized()
        )

        if not data.twilio_verify_requests:
            findings.append({
                'code': 'twilio_verify_requests_disabled',
                'level': 'info' if sandboxed else 'critical',
                'message': (
                    'Twilio webhook signature verification is disabled '
                    '(connect.settings.twilio_verify_requests=False). '
                    + ('This is a sandboxed/neutralized database, so webhook checks '
                       'still fail closed regardless -- expected for a dev/staging '
                       'environment.'
                       if sandboxed else
                       'No sandbox override or neutralization flag is active, so this '
                       'looks like a production instance running with signature '
                       'verification off. Re-enable it in Connect > Settings unless '
                       'there is a specific, current reason it needs to stay off.')
                ),
            })

        if data.rest_provider == 'twilio' and (not account_sid or not auth_token):
            findings.append({
                'code': 'twilio_credentials_missing',
                'level': 'info' if sandboxed else 'critical',
                'message': (
                    'Telephony provider is Twilio but account_sid/auth_token are not '
                    'both configured. '
                    + ('This is a sandboxed/neutralized database, so missing live '
                       'credentials is expected -- no action needed.'
                       if sandboxed else
                       'If twilio_verify_requests is (or becomes) enabled, every real '
                       'Twilio webhook will fail signature validation and calls will '
                       'be rejected -- fix this before turning verification on, not '
                       'after.')
                ),
            })

        if not config['proxy_mode']:
            findings.append({
                'code': 'proxy_mode_disabled',
                'level': 'warning',
                'message': (
                    "Odoo server config proxy_mode is off. If this instance sits "
                    "behind a reverse proxy/load balancer, Odoo reconstructs the "
                    "WRONG url (scheme, host) for anything that needs the original "
                    "request Twilio signed, so signature verification fails even "
                    "with correct credentials -- and remote_addr-based checks "
                    "elsewhere see the proxy's IP, not the caller's. Set "
                    "proxy_mode=True in odoo.conf (or --proxy-mode) if a proxy sits "
                    "in front of this instance."
                ),
            })

        for f in findings:
            log = {'critical': logger.critical, 'warning': logger.warning}.get(f['level'], logger.info)
            log('Connect security preflight [%s]: %s', f['code'], f['message'])

        return findings

    def _register_hook(self):
        """Run the security preflight every time the registry is built --
        server start, module install, and module upgrade all rebuild the
        registry, so this one hook covers all three without a cron worker.
        """
        super()._register_hook()
        self.check_security_preflight()

    @api.model
    def set_instance_uid(self, instance_uid=False):
        existing_uid = self.env["ir.config_parameter"].get_param("connect.instance_uid")
        if not existing_uid:
            if not instance_uid:
                instance_uid = str(uuid.uuid4())
            self.env["ir.config_parameter"].set_param(
                "connect.instance_uid", instance_uid
            )

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
        for field_name in self._get_protected_fields():
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
        return res

    @api.model
    def _get_protected_fields(self):
        """Display-field names masked like a password after write().

        Provider extensions with their own secrets (e.g. Connect VoiceTel's
        API key/secret) override this to add their own ``display_*`` field
        names alongside PROTECTED_FIELDS rather than duplicating the write()
        masking logic.
        """
        return list(PROTECTED_FIELDS)

    @api.model
    def _get_client_credentials(self):
        return (
            self.sudo().get_param('account_sid'),
            self.sudo().get_param('auth_token'),
        )

    @api.model
    def _get_rest_api_host(self):
        return (self.sudo().get_param('rest_api_host') or '').strip()

    @api.model
    def _get_account_migrators(self):
        """Migrator classes the account migration wizard runs, in list order.

        Extension modules must APPEND to super()'s result: appended migrators
        run after the core ones and may depend on their target-side results
        (e.g. BYOC trunks bind SIP domains and their credential lists, which
        exist on the target only once domains have migrated).
        """
        return [NumberMigrator, TwimlMigrator, DomainMigrator]

    @api.model
    def uses_compatible_rest_api(self):
        return bool(self._get_rest_api_host())

    @api.model
    def normalized_sip_domain_suffix(self):
        suffix = (self.get_param('sip_domain_suffix') or '').strip().lstrip('.')
        return suffix or DEFAULT_SIP_DOMAIN_SUFFIX

    @api.model
    def format_sip_domain_name(self, subdomain):
        return '{}.{}'.format(subdomain, self.normalized_sip_domain_suffix())

    @api.model
    def format_sip_edge_domain(self, subdomain, edge):
        suffix = self.normalized_sip_domain_suffix()
        if suffix == DEFAULT_SIP_DOMAIN_SUFFIX and edge:
            return '{}.sip.{}.twilio.com'.format(subdomain, edge)
        return self.format_sip_domain_name(subdomain)

    @api.model
    def format_sip_connect_uri(self, username, subdomain, edge=None):
        suffix = self.normalized_sip_domain_suffix()
        if suffix == DEFAULT_SIP_DOMAIN_SUFFIX and edge and edge != 'roaming':
            return '{}@{}.sip.{}.twilio.com'.format(username, subdomain, edge)
        return '{}@{}'.format(username, self.format_sip_domain_name(subdomain))

    @api.model
    def get_default_hold_music_url(self):
        url = (self.get_param('default_hold_music_url') or '').strip()
        return url or DEFAULT_HOLD_MUSIC_URL

    @api.model
    def is_webrtc_enabled(self):
        return self.get_param('webrtc_provider') != 'disabled'

    @api.model
    def parse_sip_to_user(self, to_val):
        if not isinstance(to_val, str) or not to_val.startswith('sip:'):
            return None
        at = to_val.find('@')
        if at == -1:
            return None
        user_part = to_val[4:at]
        host_part = to_val[at + 1:]
        suffix = self.normalized_sip_domain_suffix()
        if suffix == DEFAULT_SIP_DOMAIN_SUFFIX:
            if re.match(r'^.+\.sip(\.[^.]+)?\.twilio\.com$', host_part):
                return user_part
        elif host_part == suffix or host_part.endswith('.' + suffix):
            return user_part
        return None

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
            account_sid, auth_token = self._get_client_credentials()
            if not account_sid or not auth_token:
                logger.warning("Twilio credentials not configured (account_sid=%s, auth_token=%s)",
                               bool(account_sid), bool(auth_token))
                return None
            rest_api_host = self._get_rest_api_host()
            token_to_use = auth_token
            if region and not rest_api_host:
                region_auth_token = self.sudo().get_param("region_auth_token")
                token_to_use = region_auth_token if region_auth_token else auth_token
            # A custom REST host points the SDK at a Twilio-compatible provider
            # (e.g. VoiceTel). The host is then fixed, so Twilio region/edge
            # routing is moot and intentionally skipped.
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
    @api.model
    def defer_twilio_recording_delete(self, sid):
        """Delete a provider recording only after the current transaction commits.

        The delete is an irreversible external side effect. Executed inline,
        a transaction that later fails (e.g. a webhook hitting a
        serialization-failure retry) forgets the stored copy while the
        remote original is already gone — the retry then 404s and the
        recording is unrecoverable. postcommit callbacks are cleared on
        rollback, so a failed transaction never triggers the delete and the
        retry can simply re-download.
        """
        if not sid:
            return
        client = self.get_client()
        if not client:
            return

        def _delete():
            try:
                client.recordings(sid).delete()
                logger.info('Deleted recording %s from provider (post-commit)', sid)
            except Exception as e:
                logger.error('Post-commit provider delete failed for %s: %s', sid, e)

        self.env.cr.postcommit.add(_delete)

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
        account_sid, auth_token = self.sudo()._get_client_credentials()
        if not (account_sid and auth_token):
            raise ValidationError("You must set account SID and Auth token!")
        api_url_check = self.check_api_url()
        if api_url_check:
            raise ValidationError(api_url_check)
        # Each resource type syncs inside its own savepoint: one failing
        # type must not roll back the others. The provider-side API calls
        # a sub-sync makes are NOT transactional — an all-or-nothing
        # rollback discards the SIDs a sub-sync just persisted while the
        # provider keeps the created resources, so every retry re-creates
        # them (observed live: one crashing sub-sync orphaned 3 TwiML apps
        # per Sync click).
        errors = []
        for model in ("connect.twiml", "connect.domain", "connect.number",
                      "connect.outgoing_callerid", "connect.whatsapp_sender",
                      "connect.message_content_template"):
            try:
                with self.env.cr.savepoint():
                    self.env[model].sync()
            except Exception as e:
                if 'errors/20003' in str(e):
                    # Bad credentials fail every sub-sync identically;
                    # abort outright instead of reporting it six times.
                    raise ValidationError('Error authenticating requests to the Twilio API! Check your Auth Key!')
                logger.exception('Sync failed for %s:', model)
                errors.append('{}: {}'.format(
                    self.env[model]._description, format_connect_response(str(e))))
        if errors:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Sync completed with errors',
                    'message': '\n'.join(errors),
                    'type': 'warning',
                    'sticky': True,
                },
            }

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
