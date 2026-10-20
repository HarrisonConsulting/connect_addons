# -*- coding: utf-8 -*-
import logging
import random
import re
import string
import uuid
from urllib.parse import urljoin

from odoo import fields, models, api, release
from odoo.exceptions import ValidationError
if release.version_info[0] >= 19:
    from odoo.models import Constraint

from odoo.addons.connect.models.settings import debug
from .settings import MAX_EXTEN_LEN, format_connect_response
from .twiml_builder import TwiMLResponse

logger = logging.getLogger(__name__)


class User(models.Model):
    _inherit = 'connect.user'

    # Every field and method this module adds to connect.user is named
    # voiceml_* (or *_voiceml): connect_twilio declares username, sid,
    # password, domain, uri, application and the ring timeouts on this same
    # model, plus render(), get_client_identity() and the SIP-account
    # helpers. Odoo merges both classes into one, so a shared name would let
    # whichever module loads last take the other's column, comodel or calls.
    voiceml_username = fields.Char(string='VoiceML Username')

    if release.version_info[0] >= 19:
        _voiceml_username_uniq = Constraint(
            'UNIQUE(voiceml_username)', 'This VoiceML username is already defined!')
    else:
        _sql_constraints = [
            ('voiceml_username_uniq', 'UNIQUE(voiceml_username)',
             'This VoiceML username is already defined!'),
        ]

    @api.constrains('voiceml_username')
    def _check_voiceml_username(self):
        for rec in self:
            if rec.voiceml_username and not rec.voiceml_username.isalnum():
                raise ValidationError('VoiceML username must be alphanumeric!')

    @api.constrains('voiceml_sip_enabled', 'voiceml_client_enabled',
                    'voiceml_username', 'voiceml_domain')
    def _check_voiceml_account(self):
        for rec in self:
            if ((rec.voiceml_sip_enabled or rec.voiceml_client_enabled)
                    and not (rec.voiceml_username and rec.voiceml_domain)):
                raise ValidationError(
                    'Username and SIP domain are required to enable the '
                    'VoiceML SIP or web phone for user {}!'.format(rec.name))

    @api.model
    def _pbx_number_fields(self):
        return super()._pbx_number_fields() + ['voiceml_exten_number']

    originate_provider = fields.Selection(
        selection_add=[('voiceml', 'VoiceML')],
        ondelete={'voiceml': 'set null'},
    )
    message_provider = fields.Selection(
        selection_add=[('voiceml', 'VoiceML')],
        ondelete={'voiceml': 'set null'},
    )
    voiceml_exten = fields.Many2one(
        'connect.voiceml.exten', ondelete='set null', readonly=True,
        string='VoiceML Extension')
    voiceml_exten_number = fields.Char(
        related='voiceml_exten.number', store=True,
        string='VoiceML Extension Number')
    voiceml_outgoing_callerid = fields.Many2one(
        'connect.voiceml.outgoing_callerid', ondelete='set null',
        string='VoiceML Outgoing CallerID')

    voiceml_sid = fields.Char('VoiceML SIP Credential SID', readonly=True)
    voiceml_password = fields.Char(
        string='VoiceML Password',
        groups="connect.group_admin")
    voiceml_domain = fields.Many2one(
        'connect.voiceml.domain',
        string='VoiceML SIP Domain',
        ondelete='set null',
    )
    voiceml_sip_enabled = fields.Boolean('VoiceML SIP Phone Enabled')
    voiceml_client_enabled = fields.Boolean('VoiceML Web Phone Enabled')
    voiceml_sip_ring_timeout = fields.Integer(
        required=True, default=30, string='VoiceML SIP Ring Timeout')
    voiceml_client_ring_timeout = fields.Integer(
        required=True, default=10, string='VoiceML Web Client Ring Timeout')
    voiceml_uri = fields.Char('VoiceML SIP URI', compute='_get_voiceml_uri')
    voiceml_application = fields.Many2one(
        'connect.voiceml.application', string='VoiceML Application')

    @api.model
    def _default_voiceml_domain(self):
        return self.env['connect.voiceml.domain'].search([], limit=1)

    @api.depends('voiceml_username', 'voiceml_domain')
    def _get_voiceml_uri(self):
        for rec in self:
            rec.voiceml_uri = '{}@{}'.format(
                rec.voiceml_username, rec.voiceml_domain.domain_name
            ) if rec.voiceml_username and rec.voiceml_domain else ''

    def get_voiceml_client_identity(self):
        return '{}@{}'.format(self.voiceml_username, self.voiceml_domain.domain_name)

    def voiceml_caller_id(self):
        self.ensure_one()
        if self.voiceml_exten and self.voiceml_exten.number:
            return self.voiceml_exten.number
        callerid = (
            self.voiceml_outgoing_callerid.number
            or self.env['connect.voiceml.outgoing_callerid']
            .sudo().search([('is_default', '=', True)], limit=1).number
        )
        return callerid or ''

    def voiceml_pstn_caller_id(self):
        """Caller ID for calls out to the PSTN: the user's outgoing caller ID,
        then the default one. Never the extension, which is not a number."""
        self.ensure_one()
        callerid = (
            self.voiceml_outgoing_callerid.sudo().number
            or self.env['connect.voiceml.outgoing_callerid']
            .sudo().search([('is_default', '=', True)], limit=1).number
        )
        return callerid or ''

    @api.model
    def get_user_by_uri(self, userinfo):
        if not userinfo:
            return super().get_user_by_uri(userinfo)
        re_call_uri = re.compile(r'^(?:sip|client):([^@]+)@([^:;>?\s]+)?')
        found = re_call_uri.search(userinfo)
        if found:
            username, host = found.group(1), found.group(2)
            domain = [('voiceml_username', '=', username)]
            if host:
                user = self.env['connect.user'].search(
                    domain + [('voiceml_domain.domain_name', '=ilike', host)],
                    limit=1)
                if user:
                    return user
            # Host unknown to VoiceML: fall back to the username only when
            # it is unambiguous across domains.
            user = self.env['connect.user'].search(domain, limit=2)
            if len(user) == 1:
                return user
        return super().get_user_by_uri(userinfo)

    # --- SIP credential provisioning (VoiceML SIP Domains/CredentialLists) ---

    @staticmethod
    def generate_voiceml_password():
        password_chars = [
            random.choice(string.ascii_lowercase),
            random.choice(string.ascii_uppercase),
            random.choice(string.digits),
        ]
        password_chars += random.choices(string.ascii_letters + string.digits, k=9)
        random.shuffle(password_chars)
        return ''.join(password_chars)

    def _create_voiceml_sip_account(self, username, password, client=None):
        self.ensure_one()
        try:
            client = client or self.env['connect.settings']._voiceml_client()
            domain = self.voiceml_domain.sudo()
            if not domain.cred_list_sid:
                domain._ensure_cred_list(client)
            credentials = client.sip.credential_lists.credentials(domain.cred_list_sid)
            try:
                return credentials.create(username=username, password=password).sid
            except Exception as e:
                if not domain._voiceml_is_conflict(e):
                    raise
                # The username exists remotely: adopt its sid, reset the password.
                for cred in domain._voiceml_items(credentials.list, 'credentials'):
                    if cred.username == username:
                        credentials.update(cred.sid, password=password)
                        return cred.sid
                raise
        except Exception as e:
            raise ValidationError(format_connect_response(str(e)))

    def _update_voiceml_sip_password(self, password):
        self.ensure_one()
        if not self.voiceml_sid:
            return
        client = self.env['connect.settings']._voiceml_client()
        try:
            client.sip.credential_lists.credentials(
                self.voiceml_domain.cred_list_sid
            ).update(self.voiceml_sid, password=password)
        except Exception as e:
            raise ValidationError(format_connect_response(str(e)))

    @api.private
    def delete_voiceml_sip_account(self):
        self.ensure_one()
        if not self.voiceml_sid:
            return
        client = self.env['connect.settings']._voiceml_client()
        try:
            client.sip.credential_lists.credentials(
                self.voiceml_domain.cred_list_sid
            ).delete(self.voiceml_sid)
        except Exception:
            logger.exception('Failed to delete VoiceML SIP account %s', self.voiceml_username)

    @api.model
    def _voiceml_vals_with_domain(self, vals):
        """Enabling the SIP or web phone needs a domain; pick the first one
        only then, never for plain users."""
        if ((vals.get('voiceml_sip_enabled') or vals.get('voiceml_client_enabled'))
                and not vals.get('voiceml_domain')):
            domain = self._default_voiceml_domain()
            if domain:
                vals = dict(vals, voiceml_domain=domain.id)
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._voiceml_vals_with_domain(vals) for vals in vals_list]
        recs = super().create(vals_list)
        if not self.env.context.get('no_voiceml_create'):
            for rec in recs:
                try:
                    if rec.voiceml_sip_enabled and rec.sudo().voiceml_password:
                        if not self.env.context.get('skip_create_credential'):
                            rec.voiceml_sid = rec._create_voiceml_sip_account(
                                username=rec.voiceml_username, password=rec.sudo().voiceml_password)
                except Exception as e:
                    raise ValidationError(format_connect_response(str(e)))
        return recs

    def write(self, vals):
        if self.env.context.get('skip_voiceml_sync'):
            return super().write(vals)
        if 'voiceml_username' in vals and any(
                rec.voiceml_username and rec.voiceml_username != vals['voiceml_username']
                for rec in self):
            raise ValidationError('VoiceML username cannot be changed!')
        if (vals.get('voiceml_sip_enabled') or vals.get('voiceml_client_enabled')) \
                and 'voiceml_domain' not in vals:
            missing = self.filtered(lambda r: not r.voiceml_domain)
            if missing:
                domain_vals = self._voiceml_vals_with_domain(dict(vals))
                if domain_vals.get('voiceml_domain'):
                    super(User, missing).write(
                        {'voiceml_domain': domain_vals['voiceml_domain']})
        for rec in self:
            if (vals.get('voiceml_password') and rec.voiceml_username
                    and vals.get('voiceml_sip_enabled', rec.voiceml_sip_enabled)
                    and self.env["connect.settings"].get_param("voiceml_auto_sync")):
                if rec.voiceml_sid:
                    rec._update_voiceml_sip_password(vals['voiceml_password'])
                else:
                    sid = rec._create_voiceml_sip_account(
                        rec.voiceml_username, vals['voiceml_password'])
                    super(User, rec).write({'voiceml_sid': sid})
        return super().write(vals)

    def unlink(self):
        for rec in self:
            if self.env["connect.settings"].get_param("voiceml_auto_sync"):
                rec.delete_voiceml_sip_account()
        return super().unlink()

    # --- Rendering (TwiML for VoiceML) ---

    def _get_voiceml_caller_id(self, request, params):
        caller_user = self.env['connect.user'].get_user_by_uri(request.get('Caller'))
        return caller_user.voiceml_caller_id() if caller_user else request.get('Caller')

    def _get_voiceml_caller_name(self, request, params):
        caller_user = self.env['connect.user'].get_user_by_uri(request.get('Caller'))
        return params.get('CallerName') or (caller_user.name if caller_user else '')

    def get_voiceml_greeting_message(self, response):
        if self.greeting_message:
            response.say(
                self.greeting_message,
                language=self.language or 'en-US',
            )

    def render_voiceml(self, request={}, params={}):
        self.ensure_one()
        response = TwiMLResponse()
        self.get_voiceml_greeting_message(response)
        api_url = self.env['connect.settings'].sudo().get_param('api_url')
        status_url = urljoin(api_url, 'voiceml/webhook/callstatus')
        record_status_url = urljoin(api_url, 'voiceml/webhook/recordingstatus')
        dial_kwargs = {'timeout': (
            self.voiceml_sip_ring_timeout if self.voiceml_sip_enabled
            else self.voiceml_client_ring_timeout)}
        if self.env['connect.settings'].sudo().calls_are_recorded(user=self):
            dial_kwargs.update({
                'recordingStatusCallback': record_status_url,
                'record': 'record-from-answer-dual',
            })
        response.dial(**dial_kwargs).sip(
            'sip:{}'.format(self.voiceml_uri),
            statusCallbackEvent='initiated answered completed',
            statusCallback=status_url,
        )
        return response.to_string()

    # --- Softphone config (JsSIP against the OpenSIPS WSS edge) ---

    @api.model
    def get_voiceml_phone_config(self, nonce=False):
        """RPC payload the browser softphone uses to register via JsSIP.

        Unlike connect_twilio (JWT VoiceGrant to the Twilio gateway), the
        VoiceML web phone is a SIP-over-WebRTC client: it registers against
        the OpenSIPS WSS edge with the SIP credentials provisioned above.

        ``nonce`` identifies the calling browser tab. The SIP credential is
        one per user, so each tab is told its own RFC 5626 ``instance_id``
        (a UUID derived from user and nonce, stable across a tab reload) and
        registers as a separate binding instead of replacing another tab's.
        """
        has_group = self.env.user.has_group('connect.group_user') or \
            self.env.user.has_group('connect.group_admin')
        if not has_group:
            return {'ok': False}
        # Self only: the password field is admin-group, the browser needs its own.
        user = self.sudo().search([('user', '=', self.env.user.id)])
        if (not user or not user.voiceml_client_enabled
                or not (user.voiceml_username and user.voiceml_domain)):
            return {'ok': False}
        domain_name = user.voiceml_domain.domain_name
        # The registrar is per SIP domain, never an account-wide setting.
        wss_url = 'wss://%s:8443' % domain_name
        instance_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            'voiceml:{}:{}'.format(user.id, nonce or 'default')))
        return {
            'ok': True,
            'username': user.voiceml_username,
            'password': user.voiceml_password,
            'domain': user.voiceml_domain.domain_name,
            'realm': user.voiceml_domain.domain_name,
            'wss_url': wss_url,
            'instance_id': instance_id,
            'uri': user.voiceml_uri,
            'record_calls': self.env['connect.settings'].sudo().calls_are_recorded(user=user),
            'exten': user.voiceml_exten_number or '',
            'outgoing_callerid': (
                user.voiceml_outgoing_callerid.number
                or self.env['connect.voiceml.outgoing_callerid']
                .sudo().search([('is_default', '=', True)], limit=1).number
                or ''
            ),
        }
