# -*- coding: utf-8 -*-
import logging
import re
from urllib.parse import urljoin, urlparse

from odoo import fields, models, api, release
from odoo.exceptions import AccessError, ValidationError
from voiceml import Client
from voiceml.exceptions import ApiError, AuthenticationError
from voiceml.models import CreateCallRequest

from .voiceml_response import Dial, VoiceResponse

from odoo.addons.connect.models.license import ODUIST_MODULES
from odoo.addons.connect.models.settings import debug

from ..tools import (
    API_HOST_ZONE, DEFAULT_VOICETEL_API_HOST, DEFAULT_VOICETEL_SIP_SUFFIX,
    SIP_SUFFIX_ZONE, in_zone, normalize_host)

ODUIST_MODULES.append('connect_voicetel')


logger = logging.getLogger(__name__)

MAX_EXTEN_LEN = 4

VOICETEL_PROTECTED_FIELDS = [
    "display_voicetel_api_key",
]

# VoiceML's public API host, and the suffix tenant SIP domains live
# under: the registrar only authenticates *.vml.voice.tel names (ADR-066
# decision 9). Shared with the migration through ..tools.
DEFAULT_VOICETEL_STUN_SERVER = 'stun:stun.l.google.com:19302'
# The SIP over WebSocket listener is tied to the SIP domain:
# wss://<domain_name>:8443 (VoiceML Phone uses the same address).
VOICETEL_WSS_PORT = 8443

def format_connect_response(text):
    if not isinstance(text, str):
        text = str(text)
    symbol_pattern = re.compile(r"(\x08.)|\x08")
    text = symbol_pattern.sub("", text)
    color_pattern = re.compile(r"\x1b\[[\d;]+m")
    text = color_pattern.sub("", text)
    return text


# A PSTN number or extension: an optional + and digits, nothing else.
RE_DIAL_NUMBER = re.compile(r'^\+?[0-9]+$')


def strip_number(number):
    """Strip number formatting"""
    if not isinstance(number, str):
        return number
    pattern = r"[\s\(\)\-\+]"
    return re.sub(pattern, "", number).lstrip("0")


class Settings(models.Model):
    _inherit = "connect.settings"

    voicetel_account_sid = fields.Char(string="VoiceTel Account SID")
    # Never grant this to connect.group_webhook: the webhook user is the
    # identity of all public webhook controllers, and get_param() returns
    # groups-restricted fields to group members. Signature validation in
    # the controllers reads the key via sudo() and is not affected.
    voicetel_api_key = fields.Char(groups="base.group_erp_manager")
    display_voicetel_api_key = fields.Char()
    # The API key is sent to the API host and the browser registers under
    # the suffix: system administrators only, held to VoiceTel's zones.
    voicetel_api_host = fields.Char(
        string='VoiceTel API Host', default=DEFAULT_VOICETEL_API_HOST, required=True,
        groups='base.group_system',
        help='Hostname of the VoiceML REST API, without scheme or path. '
             'Must be under voicetel.com unless the system parameter '
             'connect.voicetel_allow_custom_host is set.')
    voicetel_sip_suffix = fields.Char(
        string='VoiceTel SIP Domain Suffix', default=DEFAULT_VOICETEL_SIP_SUFFIX,
        required=True, groups='base.group_system',
        help='Tenant SIP domains are created as <label>.<suffix>. The VoiceML '
             'registrar authenticates registrations and calls under this '
             'suffix only.')
    voicetel_stun_server = fields.Char(
        string='STUN Server', default=DEFAULT_VOICETEL_STUN_SERVER,
        groups='base.group_system',
        help='STUN server for the browser phone. The WSS edge forces ICE, so '
             'a symmetric NAT without STUN leaves callers with one-way audio.')
    voicetel_phone_enabled = fields.Boolean(
        string='VoiceTel Browser Phone', default=True,
        help='Load the VoiceTel web phone for users whose browser phone is '
             'enabled.')
    voicetel_balance = fields.Char(readonly=True)
    voicetel_auto_sync = fields.Boolean(default=True)
    voicetel_verify_requests = fields.Boolean(
        default=True, string="Verify VoiceTel Requests"
    )
    voicetel_fetch_call_prices = fields.Boolean(
        default=False,
        string="Fetch VoiceTel Call Prices",
        help="Fetch call prices from the VoiceML API after call completion."
    )

    @api.model
    def _voicetel_api_url(self):
        """Public URL callbacks are built on.

        The connect `api_url` parameter wins; when it is unset (a fresh
        install, the test rig) fall back to the instance's web.base.url. The
        computed connect.settings fields of the same name must not be read
        through get_param(), which would return a recordset.
        """
        # connect.api_url is the public URL set by the admin; when unset it
        # is seeded to web.base.url by set_defaults. Read the parameter
        # directly: the settings record is transient and tests set the same
        # parameter through it.
        params = self.env['ir.config_parameter'].sudo()
        url = params.get_param('connect.api_url')
        if url:
            return url
        return params.get_param('web.base.url') or ''

    @api.model
    def _voicetel_custom_host_allowed(self):
        return bool(self.env['ir.config_parameter'].sudo().get_param(
            'connect.voicetel_allow_custom_host'))

    @api.constrains('voicetel_api_host', 'voicetel_sip_suffix')
    def _check_voicetel_hosts(self):
        if self._voicetel_custom_host_allowed():
            return
        for rec in self.sudo():
            for fname, zone in (('voicetel_api_host', API_HOST_ZONE),
                                ('voicetel_sip_suffix', SIP_SUFFIX_ZONE)):
                raw = rec[fname]
                if not raw:
                    continue
                if not in_zone(normalize_host(raw), zone):
                    raise ValidationError(
                        '{} must be a hostname under {}. Set the system '
                        'parameter connect.voicetel_allow_custom_host to '
                        'allow another host.'.format(rec._fields[fname].string, zone))

    @api.model
    def _voicetel_api_host(self):
        """Bare API hostname; the default when the stored one is unusable.

        Re-checked here and not only on save: the value can arrive through
        ir.config_parameter, and the API key is sent to it.
        """
        host = normalize_host(self.sudo().get_param('voicetel_api_host'))
        if not host:
            return DEFAULT_VOICETEL_API_HOST
        if not (in_zone(host, API_HOST_ZONE) or self._voicetel_custom_host_allowed()):
            logger.warning(
                'VoiceTel API host %s is not under %s; using %s',
                host, API_HOST_ZONE, DEFAULT_VOICETEL_API_HOST)
            return DEFAULT_VOICETEL_API_HOST
        return host

    @api.model
    @api.private
    def get_voicetel_client(self):
        """VoiceML SDK client for the configured account (ADR-066 as amended).

        The ``voiceml`` package is VoiceTel's own SDK: the same
        Twilio-compatible REST surface with native ``base_url`` host config
        and typed errors (``voiceml.exceptions.ApiError`` subclasses carrying
        ``code``), so no string matching on exception text.
        """
        account_sid = self.sudo().get_param("voicetel_account_sid")
        api_key = self.sudo().get_param("voicetel_api_key")
        if not (account_sid and api_key):
            raise ValidationError("Set the VoiceTel Account SID and API key first!")
        return Client(
            account_sid=account_sid,
            api_key=api_key,
            base_url='https://{}'.format(self._voicetel_api_host()),
        )

    @api.model
    def _voicetel_check_admin(self):
        """Remote-changing VoiceTel actions are for Connect administrators."""
        if not (self.env.su or self.env.user.has_group('connect.group_admin')):
            raise AccessError(
                'Only Connect administrators can change the VoiceTel account.')

    def voicetel_sync(self):
        self._voicetel_check_admin()
        if not (
            self.sudo().get_param("voicetel_account_sid")
            and self.sudo().get_param("voicetel_api_key")
        ):
            raise ValidationError("You must set the Account SID and API key!")
        api_url_check = self.check_api_url()
        if api_url_check:
            raise ValidationError(api_url_check)
        # Each resource in its own savepoint: one failing resource is
        # reported and does not roll back or skip the others.
        errors = []
        for model in (
            "connect.voicetel.voiceml",
            "connect.voicetel.domain",
            "connect.voicetel.number",
            "connect.voicetel.outgoing_callerid",
        ):
            try:
                with self.env.cr.savepoint():
                    self.env[model].sync()
            except AuthenticationError:
                raise ValidationError(
                    'Error authenticating requests to the VoiceML API! Check your API key!'
                )
            except Exception as e:
                logger.exception("VoiceTel sync failed for %s", model)
                errors.append("%s: %s" % (model, format_connect_response(e)))
        if errors:
            self.connect_notify(
                "VoiceTel sync finished with errors: %s" % "; ".join(errors),
                title="Sync Incomplete", warning=True,
            )
        else:
            self.connect_notify(
                "VoiceTel account synced successfully", title="Sync Complete")

    @api.model
    def voicetel_get_phone_settings(self):
        """Web phone configuration for the JsSIP client.

        The payload keys mirror what the phone component consumes in
        ``initUserAgent``; the per-user half (SIP credential, domain) comes
        from ``res.users.get_voicetel_sip_user_config``.
        """
        get_param = self.sudo().get_param
        return {
            "phone_enabled": bool(get_param("voicetel_phone_enabled")),
            "user_agent": {
                "phone_sip_protocol": "wss",
                "phone_stun_server": get_param("voicetel_stun_server") or "",
            },
        }

    @api.private
    def voicetel_webhook_url(self, path):
        """Absolute Odoo URL for a VoiceML callback.

        VoiceML signs the URL as transmitted and has no edge concept, so no
        fragment is ever appended here (unlike the Twilio module's #e=edge).
        """
        api_url = self.sudo()._voicetel_api_url()
        return urljoin(api_url, path)

    @api.private
    def compute_voicetel_sip_uri(self, user):
        return "sip:{}".format(user.connect_user.voicetel_uri)

    @api.private
    def voicetel_external_call_route(self, number, callerId, status_url):
        """<Dial><Number> for a PSTN leg, built with the XML builder.

        The number and caller ID reach here from the browser and the
        directory; string formatting let them inject verbs.
        """
        if not RE_DIAL_NUMBER.match(number or ''):
            raise ValidationError('Not a dialable number: {}'.format(number))
        call_duration_limit = int(self.sudo().get_param('call_duration_limit') or 0)
        dial_kwargs = {'callerId': callerId}
        if call_duration_limit:
            dial_kwargs['timeLimit'] = call_duration_limit
        response = VoiceResponse()
        dial = Dial(**dial_kwargs)
        dial.number(
            number,
            statusCallback=status_url,
            statusCallbackMethod='POST',
            statusCallbackEvent='initiated answered completed',
        )
        response.append(dial)
        return response.to_xml()

    @api.model
    def _voicetel_dial_number(self, number):
        """E.164 for a PSTN target, an extension left as digits.

        Digits longer than an extension with no leading + are parsed with
        the company's country, so 4845551234 becomes +14845551234 and not
        +4845551234 (#230 prefixed '+' to anything over four digits).
        """
        raw = re.sub(r"[\s\(\)\-\.]", "", number or "")
        if raw.startswith("+"):
            return "+" + strip_number(raw)
        digits = strip_number(raw)
        if len(digits) <= MAX_EXTEN_LEN:
            return digits
        from odoo.addons.connect.models.res_partner import format_number
        country = self.env.company.country_id.code
        formatted = format_number(self.env["res.partner"], raw, country)
        if formatted and formatted.startswith("+"):
            return formatted
        return "+" + digits

    @api.model
    def originate_call(self, number, res_model=None, res_id=None, user=None, **kwargs):
        # Dispatch by the user's click-to-call provider; fall through to
        # other installed telephony modules when it is not VoiceTel.
        if self._get_originate_provider(user) != 'voicetel':
            return super().originate_call(
                number, res_model=res_model, res_id=res_id, user=user, **kwargs)
        self.env["oduist.license"].check_license("connect", silent=False)
        number = self._voicetel_dial_number(number)
        client = self.get_voicetel_client()
        partner_id = False
        obj = self.env[res_model].browse(res_id) if res_model and res_id else False
        if res_model == "res.partner" and obj:
            partner_id = res_id
        elif obj and hasattr(obj, "partner_id") and obj.partner_id:
            partner_id = obj.partner_id.id
        elif obj and hasattr(obj, "partner") and obj.partner:
            partner_id = obj.partner.id
        if not user:
            user = self.env.user
        pbx_user = user.connect_user
        if not pbx_user:
            raise ValidationError("User does not have a PBX account defined!")
        if not (pbx_user.voicetel_sip_enabled and pbx_user.voicetel_username
                and pbx_user.voicetel_domain):
            raise ValidationError(
                "Enable the VoiceTel SIP phone for user {} first!".format(
                    pbx_user.name))
        # The agent leg is the user's SIP address on the tenant domain: the
        # registrar rings every device registered with that credential, the
        # browser phone included (ADR-066 decision 4).
        to = self.compute_voicetel_sip_uri(user)
        exten = self.env["connect.voicetel.exten"].search(
            [("number", "=", number)], limit=1
        )
        status_url = self.voicetel_webhook_url("voicetel/webhook/callstatus")
        if exten:
            # An extension-less caller must still present an identity: an
            # empty caller ID makes the platform substitute a number of its own.
            callerId = pbx_user.voicetel_caller_id()
            # Rendering the destination dialplan is system work: it reads the
            # callee's connect.user, which the record rules keep private to
            # its owner. Without sudo, calling a colleague's extension fails
            # with an AccessError on connect.user.
            voiceml = str(exten.sudo().render())
        else:
            callerId = pbx_user.voicetel_pstn_caller_id()
            if not callerId:
                raise ValidationError(
                    "Select a default outgoing caller ID first!")
            voiceml = self.voicetel_external_call_route(
                number, callerId, status_url
            )
        record = self.calls_are_recorded(user=pbx_user)
        record_status_url = self.voicetel_webhook_url(
            "voicetel/webhook/recordingstatus")
        debug(self, 'Originate destination VoiceML: {}'.format(voiceml))
        channel = client.calls.create(CreateCallRequest(
            To=to,
            From=callerId,
            Twiml=voiceml,
            StatusCallback=status_url,
            StatusCallbackMethod='POST',
            Record=record,
            RecordingChannels="dual",
            RecordingStatusCallback=record_status_url,
            RecordingStatusCallbackMethod='POST',
            RecordingStatusCallbackEvent=["completed"],
            StatusCallbackEvent=["initiated", "answered", "completed"],
        ))
        self.env["connect.channel"].sudo().create(
            {
                "sid": channel.sid,
                "technical_direction": "outbound-api",
                "call_type": 'phone',
                "caller_user": user.id,
                "caller_pbx_user": pbx_user.id,
                "partner": partner_id,
                "called": number,
                "caller": callerId,
            }
        )

    def get_voicetel_balance(self):
        """Fetch the current VoiceML account balance."""
        try:
            client = self.get_voicetel_client()
            try:
                balance_item = client.balance.fetch()
                currency = getattr(balance_item, 'currency', 'USD')
                balance_value = getattr(balance_item, 'balance', '0.00')
                balance = "${} {}".format(balance_value, currency)
            except Exception as balance_error:
                if (
                    isinstance(balance_error, ApiError)
                    and balance_error.status_code == 404
                ):
                    balance = "Balance API not available for this account"
                    self.set_param('voicetel_balance', balance)
                    self.connect_notify(
                        "VoiceTel Balance: {}.".format(balance),
                        title="Balance Info",
                    )
                    return balance
                else:
                    raise balance_error
            self.set_param('voicetel_balance', balance)
            self.connect_notify(
                "VoiceTel Balance: {}".format(balance), title="Balance Update"
            )
            return balance
        except Exception as e:
            error_msg = "Failed to fetch VoiceTel balance: {}".format(str(e))
            self.connect_notify(error_msg, title="Balance Error", warning=True)
            raise ValidationError(error_msg)

    def write(self, vals):
        if self.env.context.get("skip_protected_fields"):
            return super(Settings, self).write(vals)
        res = super(Settings, self).write(vals)
        changed_fields = {}
        for field_name in VOICETEL_PROTECTED_FIELDS:
            if vals.get(field_name):
                changed_fields.update(
                    {
                        field_name.replace("display_", ""): vals.get(
                            field_name
                        ),
                        field_name: "*" * len(vals.get(field_name)),
                    }
                )
        if changed_fields:
            self.with_context(
                skip_protected_fields=True
            ).sudo().write(changed_fields)
        if release.version_info[0] >= 17:
            self.env.registry.clear_cache()
        else:
            self.clear_caches()
        return res
