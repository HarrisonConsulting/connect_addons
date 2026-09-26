# -*- coding: utf-8 -*-
"""VoiceTel account on connect.settings.

connect_voicetel is a standalone provider: it never routes through
connect_twilio's client, so every credential and REST call lives here,
under its own ``_voicetel_client()`` (never ``get_client()`` — that name is
connect_twilio's, and defining it here would let whichever provider module
loads last silently steal the other's REST calls).
"""
import logging
from urllib.parse import urljoin, urlparse

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .voicetel_response import VoiceResponse, Dial

logger = logging.getLogger(__name__)

MAX_EXTEN_LEN = 4
VOICETEL_DEFAULT_HOST = 'voiceml.voicetel.com'

VOICETEL_PROTECTED_FIELDS = [
    'display_voicetel_api_key',
]


def format_connect_response(text):
    if not isinstance(text, str):
        text = str(text)
    return text


def strip_number(number):
    import re
    if not isinstance(number, str):
        return number
    return re.sub(r'[\s\(\)\-\+]', '', number).lstrip('0')


class VoicetelSettings(models.Model):
    _inherit = 'connect.settings'

    voicetel_account_sid = fields.Char(
        string='VoiceTel Account SID',
        help="Account SID from your VoiceTel account; identifies your "
             "account on the VoiceML REST API.")
    # Never grant this to connect.group_webhook: the webhook user is the
    # identity of every public webhook controller, and get_param() returns
    # groups-restricted fields to group members (see connect_twilio's
    # auth_token for the same rule). Controllers read it with sudo(),
    # which is unaffected.
    voicetel_api_key = fields.Char(
        groups='base.group_erp_manager',
        help="API key from your VoiceTel account. This one key both "
             "authenticates the VoiceML REST API and signs/verifies "
             "webhooks (X-Twilio-Signature) — VoiceTel issues no separate "
             "webhook secret.")
    display_voicetel_api_key = fields.Char()
    voicetel_rest_host = fields.Char(
        default=VOICETEL_DEFAULT_HOST,
        help="Hostname of the VoiceML REST API. Leave the default unless "
             "VoiceTel support tells you otherwise.")
    voicetel_wss_url = fields.Char(
        string='WebRTC WSS URL',
        help="wss:// URL of the VoiceTel SIP registrar node the browser "
             "softphone registers against, e.g. wss://<node>:8443. "
             "Required before any user enables the VoiceTel web phone. "
             "VoiceTel's registrar nodes: east-1 3.220.193.70, "
             "east-2 3.12.226.65, west-1 52.9.10.85, all on port 8443.")
    voicetel_verify_requests = fields.Boolean(
        default=True, string='Verify VoiceTel Requests',
        help="Reject inbound VoiceTel webhooks whose X-Twilio-Signature "
             "does not match the API key. Leave enabled in production.")
    voicetel_auto_sync = fields.Boolean(
        default=True,
        help="Push every local change (numbers, SIP credentials, "
             "applications) to VoiceTel immediately instead of only on "
             "the next manual sync.")

    def write(self, vals):
        if self.env.context.get('skip_protected_fields'):
            return super().write(vals)
        res = super().write(vals)
        changed_fields = {}
        for field_name in VOICETEL_PROTECTED_FIELDS:
            if vals.get(field_name):
                changed_fields.update({
                    field_name.replace('display_', ''): vals.get(field_name),
                    field_name: '*' * len(vals.get(field_name)),
                })
        if changed_fields:
            self.with_context(skip_protected_fields=True).sudo().write(changed_fields)
        return res

    @api.model
    def _voicetel_base_url(self):
        host = (self.sudo().get_param('voicetel_rest_host') or '').strip()
        if not host:
            host = VOICETEL_DEFAULT_HOST
        if '://' in host:
            return host.rstrip('/')
        return 'https://{}'.format(host)

    @api.model
    def _voicetel_client(self):
        """VoiceTel's own REST client (the voiceml SDK) — never get_client(),
        which is connect_twilio's name for the same idea. Every VoiceTel
        model calls this, not connect.settings.get_client()."""
        try:
            from voiceml import Client
        except ImportError:
            raise ValidationError(
                "The 'voiceml' python package is not installed.")
        try:
            account_sid = self.sudo().get_param('voicetel_account_sid')
            api_key = self.sudo().get_param('voicetel_api_key')
            return Client(
                account_sid=account_sid, api_key=api_key,
                base_url=self._voicetel_base_url())
        except Exception as e:
            if 'Credentials are required' in str(e):
                raise ValidationError('Set VoiceTel API keys first!')
            raise

    @api.model
    def get_media_auth(self, media_url):
        """VoiceTel recording media is Basic-authed with the tenant key.

        Only attach credentials for VoiceTel's own REST host: a foreign
        media URL (e.g. a presigned S3 bucket) must never receive it.
        """
        host = (urlparse(media_url or '').hostname or '').lower()
        base_host = (urlparse(self._voicetel_base_url()).hostname or '').lower()
        if not base_host or host != base_host:
            return super().get_media_auth(media_url)
        account_sid = self.sudo().get_param('voicetel_account_sid')
        api_key = self.sudo().get_param('voicetel_api_key')
        if not (account_sid and api_key):
            return super().get_media_auth(media_url)
        return (account_sid, api_key)

    def _webhook_url(self, path):
        api_url = self.sudo().get_param('api_url')
        return urljoin(api_url, path)

    @api.model
    def _validate_voicetel_request(self, httprequest, params):
        """Validate an inbound VoiceTel webhook and fail closed on every
        missing precondition — the same policy connect_twilio applies to
        its own webhooks (_validate_twilio_request), kept here as its own
        method (never that name) so it can never be shadowed by, or shadow,
        connect_twilio's version of the same idea.
        """
        from .webhook import normalize_url, valid_request
        settings = self.sudo()
        if not settings.get_param('voicetel_verify_requests'):
            logger.critical(
                'SECURITY: VoiceTel webhook signature verification is '
                'disabled; rejecting the request.')
            return False
        secret = settings.get_param('voicetel_api_key')
        if not secret:
            logger.critical(
                'SECURITY: VoiceTel webhook signature verification has no '
                'API key; rejecting the request.')
            return False
        url = normalize_url(httprequest.url)
        signature = httprequest.headers.get('X-Twilio-Signature', '')
        request_valid = valid_request(url, params, signature, secret)
        if not request_valid:
            if httprequest.url.startswith('http:'):
                logger.error('VoiceTel requires HTTPS to be set up!')
            else:
                logger.error('VoiceTel request signature is not valid!')
        return request_valid

    def sync_voicetel(self):
        """Bound to the VoiceTel settings form's own button. Named apart
        from connect.settings.sync() (connect_twilio's button) so installing
        both never lets one provider's sync silently replace the other's."""
        if not (self.sudo().get_param('voicetel_account_sid')
                and self.sudo().get_param('voicetel_api_key')):
            raise ValidationError('You must set Account SID and API key!')
        api_url_check = self.check_api_url()
        if api_url_check:
            raise ValidationError(api_url_check)
        try:
            self.env['connect.voicetel.application'].sync()
            self.env['connect.voicetel.domain'].sync()
            self.env['connect.voicetel.number'].sync()
            self.env['connect.voicetel.outgoing_callerid'].sync()
            self.connect_notify(
                'VoiceTel account synced successfully', title='Sync Complete')
        except ValidationError:
            raise
        except Exception as e:
            if '20003' in str(e):
                raise ValidationError(
                    'Error authenticating requests to the VoiceTel API! '
                    'Check your API key!')
            raise

    def compute_sip_uri(self, user):
        return 'sip:{}'.format(user.connect_user.voicetel_uri)

    def get_external_call_route(self, number, callerId, status_url):
        call_duration_limit = int(self.sudo().get_param('call_duration_limit'))
        response = VoiceResponse()
        dial = response.append(Dial(callerId=callerId, timeLimit=call_duration_limit))
        dial.number(
            number, statusCallback=status_url,
            statusCallbackEvent='initiated answered completed')
        return response.to_xml()

    @api.model
    def originate_call(self, number, res_model=None, res_id=None, user=None, **kwargs):
        if self._get_originate_provider(user) != 'voicetel':
            return super().originate_call(
                number, res_model=res_model, res_id=res_id, user=user, **kwargs)
        from odoo.addons.connect.models.settings import debug
        from voiceml.models import CreateCallRequest
        number = strip_number(number)
        if len(number) > MAX_EXTEN_LEN:
            number = '+{}'.format(number)
        client = self._voicetel_client()
        partner_id = False
        caller_name = ''
        obj = self.env[res_model].browse(res_id) if res_model and res_id else False
        if res_model == 'res.partner' and obj:
            partner_id = res_id
            caller_name = obj.display_name
        elif obj and hasattr(obj, 'partner_id') and obj.partner_id:
            partner_id = obj.partner_id.id
            caller_name = obj.partner_id.display_name
        elif obj and hasattr(obj, 'partner') and obj.partner:
            partner_id = obj.partner.id
            caller_name = obj.partner.display_name
        if not user:
            user = self.env.user
        if not user.connect_user:
            raise ValidationError('User does not have a SIP username defined!')
        to = self.compute_sip_uri(user)
        exten = self.env['connect.voicetel.exten'].search(
            [('number', '=', number)], limit=1)
        status_url = self._webhook_url('voicetel/webhook/callstatus')
        if exten:
            callerId = user.connect_user.voicetel_caller_id()
            twiml = exten.sudo().render()
        else:
            default_number = self.env['connect.voicetel.outgoing_callerid'].search(
                [('is_default', '=', True)], limit=1)
            callerId = (
                user.connect_user.voicetel_outgoing_callerid.number
                or default_number.number)
            twiml = self.get_external_call_route(number, callerId, status_url)
        debug(self, 'Originate destination VoiceTel XML: {}'.format(twiml))
        record = self.calls_are_recorded(user=user.connect_user)
        record_status_url = self._webhook_url('voicetel/webhook/recordingstatus')
        channel = client.calls.create(CreateCallRequest(
            to=to,
            from_=callerId,
            twiml=twiml,
            status_callback=status_url,
            status_callback_event=['initiated', 'answered', 'completed'],
            record=record,
            recording_channels='dual',
            recording_status_callback=record_status_url,
            recording_status_callback_event='completed',
        ))
        self.env['connect.channel'].sudo().create({
            'sid': channel.sid,
            'technical_direction': 'outbound-api',
            'call_type': 'phone',
            'caller_user': user.id,
            'caller_pbx_user': user.connect_user.id,
            'partner': partner_id,
            'called': number,
            'caller': callerId,
        })
