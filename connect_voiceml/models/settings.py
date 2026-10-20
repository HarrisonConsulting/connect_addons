# -*- coding: utf-8 -*-
import logging
import re
from urllib.parse import urljoin, urlparse

from odoo import fields, models, api, release
from odoo.exceptions import AccessError, UserError, ValidationError

from voiceml import Client
from voiceml.models import CreateCallRequest

from odoo.addons.connect.models.settings import debug

logger = logging.getLogger(__name__)

MAX_EXTEN_LEN = 4
VOICEML_DEFAULT_BASE_URL = "https://voiceml.voicetel.com"

VOICEML_PROTECTED_FIELDS = [
    "display_voiceml_api_key",
]


def format_connect_response(text):
    if not isinstance(text, str):
        text = str(text)
    return re.sub(r"\x1b\[[\d;]+m", "", text)


def strip_number(number):
    if not isinstance(number, str):
        return number
    return re.sub(r"[\s\(\)\-\+]", "", number).lstrip("0")


class Settings(models.Model):
    _inherit = "connect.settings"

    rest_provider = fields.Selection(
        selection_add=[("voiceml", "VoiceML")],
        ondelete={"voiceml": "set null"},
    )
    # Every field and method this module adds to connect.settings is named
    # voiceml_* (or *_voiceml): connect_twilio owns account_sid, get_client(),
    # sync() and friends on this same model, and Odoo merges both classes
    # into one, so a shared name lets whichever module loads last take over
    # the other's column or calls.
    voiceml_account_sid = fields.Char(string="VoiceML Account SID")
    # API key is both the VoiceML SDK auth token and the X-Twilio-Signature
    # verification key for inbound webhooks. Admin-only, like connect_twilio's
    # auth_token: the webhook controllers read it via sudo().
    voiceml_api_key = fields.Char(groups="base.group_erp_manager")
    display_voiceml_api_key = fields.Char()
    voiceml_base_url = fields.Char(
        string="VoiceML API URL",
        groups="base.group_system",
        help="Base URL of the VoiceML REST API, e.g. "
        "https://voiceml.voicetel.com. The VoiceML SDK is pointed here.",
    )
    voiceml_wss_url = fields.Char(
        string="WebRTC WSS URL",
        groups="base.group_system",
        help="wss:// URL of the OpenSIPS registrar the browser softphone "
        "registers against (e.g. wss://<node>:8443).",
    )
    voiceml_auto_sync = fields.Boolean(default=True)
    voiceml_verify_requests = fields.Boolean(
        default=True, string="Verify VoiceML Requests",
        help="Off rejects every VoiceML webhook. It never lets an "
        "unsigned request through.",
    )

    @api.constrains("voiceml_base_url", "voiceml_wss_url")
    def _check_voiceml_endpoints(self):
        """The API key is sent to voiceml_base_url, so it must be https and a
        VoiceTel host unless an administrator opted in to a custom host."""
        allow_custom = self.env["ir.config_parameter"].sudo().get_param(
            "connect.voiceml_allow_custom_host")
        for rec in self.sudo():
            for fname, scheme in (("voiceml_base_url", "https"), ("voiceml_wss_url", "wss")):
                value = (rec[fname] or "").strip()
                if not value:
                    continue
                try:
                    parsed = urlparse(value)
                    host = (parsed.hostname or "").lower()
                    userinfo = parsed.username or parsed.password
                except ValueError:
                    raise ValidationError("%s is not a valid URL." % fname)
                if parsed.scheme != scheme or not host or userinfo:
                    raise ValidationError(
                        "%s must be a %s:// URL without credentials." % (fname, scheme))
                if not allow_custom and not (
                        host == "voicetel.com" or host.endswith(".voicetel.com")):
                    raise ValidationError(
                        "%s must be a *.voicetel.com host. Set the system parameter "
                        "connect.voiceml_allow_custom_host to allow another host." % fname)

    @api.model
    def _voiceml_api_host(self):
        """Host of the VoiceML REST API: the configured base URL, or the
        SDK's default when none is set."""
        base = self.sudo().get_param('voiceml_base_url') or VOICEML_DEFAULT_BASE_URL
        return (urlparse(base).hostname or '').lower()

    @api.model
    def _get_media_auth(self, media_url):
        """Recording media on VoiceML is Basic-authed with the tenant key.

        Only attach credentials for our own API host: an externally-hosted
        media URL (presigned S3 bucket) must not receive the API key.
        """
        parsed = urlparse(media_url or '')
        host = (parsed.hostname or '').lower()
        base_host = self._voiceml_api_host()
        if parsed.scheme != 'https':
            # Never send the API key over cleartext.
            return None
        if not base_host or host != base_host:
            return super()._get_media_auth(media_url)
        account_sid = self.sudo().get_param('voiceml_account_sid')
        api_key = self.sudo().get_param('voiceml_api_key')
        if not (account_sid and api_key):
            return super()._get_media_auth(media_url)
        return (account_sid, api_key)

    @api.model
    def _voiceml_client(self):
        try:
            account_sid = self.sudo().get_param("voiceml_account_sid")
            api_key = self.sudo().get_param("voiceml_api_key")
            base_url = self.sudo().get_param("voiceml_base_url")
            kwargs = {"account_sid": account_sid, "api_key": api_key}
            if base_url:
                kwargs["base_url"] = base_url
            return Client(**kwargs)
        except Exception as e:
            if "Credentials are required" in str(e):
                raise ValidationError("Set VoiceML API keys first!")
            raise

    def _voiceml_webhook_url(self, path):
        api_url = self.sudo().get_param("api_url")
        return urljoin(api_url, path)

    def sync_voiceml(self):
        if not (self.env.is_superuser() or self.env.user.has_group('connect.group_admin')):
            raise AccessError("Only Connect administrators can sync VoiceML.")
        if not (
            self.sudo().get_param("voiceml_account_sid")
            and self.sudo().get_param("voiceml_api_key")
        ):
            raise ValidationError("You must set Account SID and API key!")
        api_url_check = self.check_api_url()
        if api_url_check:
            raise ValidationError(api_url_check)
        errors = []
        for model in (
            "connect.voiceml.application",
            "connect.voiceml.domain",
            "connect.voiceml.number",
            "connect.voiceml.outgoing_callerid",
        ):
            try:
                with self.env.cr.savepoint():
                    self.env[model].sync()
            except Exception as e:
                response = getattr(e, 'response', None)
                if (
                    '20003' in str(e)
                    or getattr(e, 'status_code', None) == 401
                    or getattr(response, 'status_code', None) == 401
                ):
                    raise ValidationError(
                        'Error authenticating requests to the VoiceML API! Check your API key!'
                    )
                logger.exception("VoiceML sync failed for %s", model)
                errors.append("%s: %s" % (model, format_connect_response(str(e))))
        if errors:
            self.connect_notify(
                "VoiceML sync finished with errors: %s" % "; ".join(errors),
                title="Sync Incomplete", warning=True,
            )
        else:
            self.connect_notify(
                "VoiceML account synced successfully", title="Sync Complete"
            )

    def compute_voiceml_sip_uri(self, user):
        return "sip:{}".format(user.connect_user.voiceml_uri)

    def get_voiceml_external_call_route(self, number, callerId, status_url):
        call_duration_limit = int(self.sudo().get_param('call_duration_limit'))
        from .twiml_builder import TwiMLResponse
        response = TwiMLResponse()
        response.dial(
            callerId=callerId, timeLimit=call_duration_limit
        ).number(
            number, statusCallback=status_url,
            statusCallbackEvent='initiated answered completed',
        )
        return response.to_string()

    def _voiceml_dial_number(self, number):
        """E.164 for a PSTN target. Digits longer than an extension with no
        leading + are parsed with the company country, so 4845551234 becomes
        +14845551234 and not +4845551234."""
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
        if self._get_originate_provider(user) != 'voiceml':
            return super().originate_call(
                number, res_model=res_model, res_id=res_id, user=user, **kwargs)
        raw_number = number
        number = strip_number(number)
        if len(number) > MAX_EXTEN_LEN:
            number = self._voiceml_dial_number(raw_number)
        partner_id = False
        caller_name = ""
        obj = self.env[res_model].browse(res_id) if res_model and res_id else False
        if res_model == "res.partner" and obj:
            partner_id = res_id
            caller_name = obj.display_name
        elif obj and hasattr(obj, "partner_id") and obj.partner_id:
            partner_id = obj.partner_id.id
            caller_name = obj.partner_id.display_name
        elif obj and hasattr(obj, "partner") and obj.partner:
            partner_id = obj.partner.id
            caller_name = obj.partner.display_name
        if not user:
            user = self.env.user
        if not user.connect_user:
            raise ValidationError("User does not have a SIP username defined!")
        exten = self.env["connect.voiceml.exten"].search(
            [("number", "=", number)], limit=1
        )
        if exten:
            callerId = user.connect_user.voiceml_caller_id()
        else:
            callerId = user.connect_user.voiceml_pstn_caller_id()
        if not callerId:
            raise UserError(
                "No caller ID: set an outgoing caller ID on the user or a "
                "default VoiceML outgoing caller ID before placing calls.")
        client = self._voiceml_client()
        to = self.compute_voiceml_sip_uri(user)
        status_url = self._voiceml_webhook_url("voiceml/webhook/callstatus")
        if exten:
            twiml = exten.sudo().render()
        else:
            twiml = self.get_voiceml_external_call_route(number, callerId, status_url)
        debug(self, 'Originate destination TwiML: {}'.format(twiml))
        record = self.calls_are_recorded(user=user.connect_user)
        record_status_url = self._voiceml_webhook_url("voiceml/webhook/recordingstatus")
        channel = client.calls.create(CreateCallRequest(
            to=to,
            from_=callerId,
            twiml=twiml,
            status_callback=status_url,
            status_callback_event=["initiated", "answered", "completed"],
            record=record,
            recording_channels="dual",
            recording_status_callback=record_status_url,
            recording_status_callback_event="completed",
        ))
        self.env["connect.channel"].sudo().create(
            {
                "sid": channel.sid,
                "technical_direction": "outbound-api",
                "call_type": 'phone',
                "caller_user": user.id,
                "caller_pbx_user": user.connect_user.id,
                "partner": partner_id,
                "called": number,
                "caller": callerId,
            }
        )

    def write(self, vals):
        if self.env.context.get("skip_protected_fields"):
            return super(Settings, self).write(vals)
        res = super(Settings, self).write(vals)
        changed_fields = {}
        for field_name in VOICEML_PROTECTED_FIELDS:
            if vals.get(field_name):
                changed_fields.update(
                    {
                        field_name.replace("display_", ""): vals.get(field_name),
                        field_name: "*" * len(vals.get(field_name)),
                    }
                )
        if changed_fields:
            self.with_context(skip_protected_fields=True).sudo().write(changed_fields)
        if release.version_info[0] >= 17:
            self.env.registry.clear_cache()
        else:
            self.clear_caches()
        return res
