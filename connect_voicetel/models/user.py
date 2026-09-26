# -*- coding: utf-8 -*-
import hashlib
import logging
import random
import re
import string
from urllib.parse import urljoin

from odoo import fields, models, api
from odoo.exceptions import ValidationError
from odoo.models import Constraint

from odoo.addons.connect.models.settings import debug
from .settings import format_connect_response
from .voicetel_response import VoiceResponse, Dial

logger = logging.getLogger(__name__)

# Each open browser tab mints its own VoiceTel SIP credential (see
# VoicetelUser._get_voicetel_client_token) so concurrent tabs don't rotate
# a shared password out from under each other. Bounds how many a single
# connect.user can accumulate from tabs that were never cleanly closed.
VOICETEL_MAX_BROWSER_CREDENTIALS = 5


class VoicetelBrowserCredential(models.Model):
    """One VoiceTel SIP credential per (connect.user, browser tab/session).

    Unlike a Twilio JWT (stateless, so N tabs can share one identity), a
    VoiceTel SIP password is stateful — rotating it invalidates it
    everywhere. Keying one credential per browser-tab nonce lets concurrent
    tabs of the same user register independently instead of racing to
    rotate a single shared password out from under each other.
    """
    _name = 'connect.voicetel.browser_credential'
    _description = 'VoiceTel Browser SIP Credential'
    _log_access = False

    user = fields.Many2one('connect.user', required=True, ondelete='cascade', index=True)
    nonce = fields.Char(
        required=True,
        help="Hash of the browser tab/session nonce this credential was minted for.")
    sid = fields.Char(required=True, help="SID of the VoiceTel SIP credential for this browser tab/session.")

    # "user" is a reserved word in PostgreSQL — must be quoted in raw SQL or
    # this silently fails to create the constraint (Odoo logs a schema
    # warning and moves on rather than failing the module load).
    _user_nonce_uniq = Constraint('UNIQUE("user", nonce)', 'A browser credential already exists for this tab session.')


class VoicetelUser(models.Model):
    _inherit = 'connect.user'

    # Every field VoiceTel needs on the shared connect.user is namespaced
    # voicetel_* so it can never collide with the equivalent field another
    # provider module (connect_twilio) already owns on this same model.
    voicetel_username = fields.Char(string='VoiceTel Username')
    voicetel_sid = fields.Char('VoiceTel SIP Credential SID', readonly=True)
    voicetel_password = fields.Char(
        string='VoiceTel Password',
        groups='connect.group_admin,connect.group_user')
    voicetel_domain = fields.Many2one(
        'connect.voicetel.domain',
        string='VoiceTel SIP Domain',
        ondelete='cascade',
        default=lambda self: self._default_voicetel_domain(),
    )
    voicetel_uri = fields.Char('VoiceTel SIP URI', compute='_get_voicetel_uri')
    voicetel_sip_enabled = fields.Boolean('VoiceTel SIP Phone Enabled')
    voicetel_client_enabled = fields.Boolean('VoiceTel Web Phone Enabled')
    voicetel_sip_ring_timeout = fields.Integer(
        required=True, default=30, string='VoiceTel SIP Ring Timeout')
    voicetel_client_ring_timeout = fields.Integer(
        required=True, default=10, string='VoiceTel Web Client Ring Timeout')
    voicetel_application = fields.Many2one(
        'connect.voicetel.application', string='VoiceTel Application')

    voicetel_exten = fields.Many2one(
        'connect.voicetel.exten', ondelete='set null', readonly=True,
        string='VoiceTel Extension')
    voicetel_exten_number = fields.Char(
        related='voicetel_exten.number', store=True,
        string='VoiceTel Extension Number')
    voicetel_outgoing_callerid = fields.Many2one(
        'connect.voicetel.outgoing_callerid',
        string='VoiceTel Outgoing CallerID')

    browser_credential_ids = fields.One2many(
        'connect.voicetel.browser_credential', 'user', readonly=True,
        help="Per-browser-tab VoiceTel SIP credentials, kept separate from "
             "the desk-phone credential (field 'voicetel_sid') so minting/"
             "rotating one never disturbs an already-registered desk phone.")

    _voicetel_username_uniq = Constraint(
        'UNIQUE(voicetel_username)', 'This VoiceTel username is already defined!')

    @api.constrains('voicetel_username')
    def _check_voicetel_username(self):
        for rec in self:
            if rec.voicetel_username and not rec.voicetel_username.isalnum():
                raise ValidationError('VoiceTel username must be alphanumeric!')

    @api.constrains('voicetel_sip_enabled', 'voicetel_client_enabled',
                     'voicetel_username', 'voicetel_domain')
    def _check_voicetel_account(self):
        for rec in self:
            if ((rec.voicetel_sip_enabled or rec.voicetel_client_enabled)
                    and not (rec.voicetel_username and rec.voicetel_domain)):
                raise ValidationError(
                    'Username and SIP domain are required to enable the '
                    'VoiceTel SIP or web phone for user {}!'.format(rec.name))

    @api.model
    def _pbx_number_fields(self):
        return super()._pbx_number_fields() + ['voicetel_exten_number']

    originate_provider = fields.Selection(
        selection_add=[('voicetel', 'VoiceTel')],
        ondelete={'voicetel': 'set null'},
    )
    message_provider = fields.Selection(
        selection_add=[('voicetel', 'VoiceTel')],
        ondelete={'voicetel': 'set null'},
    )

    @api.model
    def _default_voicetel_domain(self):
        self.env.cr.execute(
            "SELECT 1 FROM information_schema.tables"
            " WHERE table_name = 'connect_voicetel_domain'")
        if not self.env.cr.fetchone():
            return False
        return self.env['connect.voicetel.domain'].search([], limit=1)

    @api.depends('voicetel_username', 'voicetel_domain')
    def _get_voicetel_uri(self):
        for rec in self:
            rec.voicetel_uri = '{}@{}'.format(
                rec.voicetel_username, rec.voicetel_domain.domain_name
            ) if rec.voicetel_username and rec.voicetel_domain else ''

    def get_voicetel_client_identity(self):
        return '{}@{}'.format(self.voicetel_username, self.voicetel_domain.domain_name)

    def voicetel_caller_id(self):
        self.ensure_one()
        if self.voicetel_exten and self.voicetel_exten.number:
            return self.voicetel_exten.number
        callerid = (
            self.voicetel_outgoing_callerid.number
            or self.env['connect.voicetel.outgoing_callerid']
            .sudo().search([('is_default', '=', True)], limit=1).number
        )
        return callerid or ''

    @api.model
    def get_user_by_uri(self, userinfo):
        if not userinfo:
            return super().get_user_by_uri(userinfo)
        re_call_uri = re.compile(r'^(?:sip|client):([^@]+)@')
        found_username = re_call_uri.search(userinfo)
        if found_username:
            user = self.env['connect.user'].search([
                ('voicetel_username', '=', found_username.group(1))])
            if user:
                return user
        return super().get_user_by_uri(userinfo)

    # --- SIP credential provisioning (VoiceTel SIP Domains/CredentialLists) ---

    @staticmethod
    def generate_voicetel_password():
        password_chars = [
            random.choice(string.ascii_lowercase),
            random.choice(string.ascii_uppercase),
            random.choice(string.digits),
        ]
        password_chars += random.choices(string.ascii_letters + string.digits, k=9)
        random.shuffle(password_chars)
        return ''.join(password_chars)

    def _create_sip_account(self, username, password, client=None):
        self.ensure_one()
        try:
            client = client or self.env['connect.settings']._voicetel_client()
            credential = (
                client.sip.credential_lists
                .credentials(self.voicetel_domain.cred_list_sid)
                .create(username=username, password=password)
            )
            return credential.sid
        except Exception as e:
            raise ValidationError(format_connect_response(str(e)))

    def _update_sip_password(self, password):
        self.ensure_one()
        if not self.voicetel_sid:
            return
        client = self.env['connect.settings']._voicetel_client()
        try:
            client.sip.credential_lists.credentials(
                self.voicetel_domain.cred_list_sid
            ).update(self.voicetel_sid, password=password)
        except Exception as e:
            raise ValidationError(format_connect_response(str(e)))

    def delete_sip_account(self):
        self.ensure_one()
        if not self.voicetel_sid:
            return
        client = self.env['connect.settings']._voicetel_client()
        try:
            client.sip.credential_lists.credentials(
                self.voicetel_domain.cred_list_sid
            ).delete(self.voicetel_sid)
        except Exception:
            logger.exception('Failed to delete SIP account %s', self.voicetel_username)

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        if not self.env.context.get('no_voicetel_create'):
            for rec in recs:
                try:
                    if rec.voicetel_sip_enabled and rec.voicetel_password:
                        if not self.env.context.get('skip_create_credential'):
                            rec.voicetel_sid = rec._create_sip_account(
                                username=rec.voicetel_username, password=rec.voicetel_password)
                except Exception as e:
                    raise ValidationError(format_connect_response(str(e)))
        return recs

    def write(self, vals):
        if self.env.context.get('skip_voicetel_sync'):
            return super().write(vals)
        if 'voicetel_username' in vals and any(
                rec.voicetel_username and rec.voicetel_username != vals['voicetel_username']
                for rec in self):
            raise ValidationError('VoiceTel username cannot be changed!')
        for rec in self:
            if vals.get('voicetel_password') and self.env['connect.settings'].get_param('voicetel_auto_sync'):
                if rec.voicetel_sid:
                    rec._update_sip_password(vals['voicetel_password'])
                else:
                    vals['voicetel_sid'] = rec._create_sip_account(
                        rec.voicetel_username, vals['voicetel_password'])
        return super().write(vals)

    def unlink(self):
        for rec in self:
            if self.env['connect.settings'].get_param('voicetel_auto_sync'):
                rec.delete_sip_account()
        return super().unlink()

    # --- Rendering (call-control XML for VoiceTel) ---
    #
    # Named render_voicetel(), not render(): connect_twilio also defines
    # render() on this same shared model, and without super() calls in
    # either implementation, whichever module's class the ORM merges last
    # would fully replace the other's — silently breaking every user of
    # whichever provider lost. Every VoiceTel call site below invokes this
    # name explicitly, so it can never be shadowed by another provider.

    def _get_caller_id(self, request, params):
        caller_user = self.env['connect.user'].get_user_by_uri(request.get('Caller'))
        return caller_user.voicetel_caller_id() if caller_user else request.get('Caller')

    def _get_caller_name(self, request, params):
        caller_user = self.env['connect.user'].get_user_by_uri(request.get('Caller'))
        return params.get('CallerName') or (caller_user.name if caller_user else '')

    def get_voicetel_greeting_message(self, response):
        if self.greeting_message:
            response.say(
                self.greeting_message,
                language=self.language or 'en-US',
            )

    def render_voicetel(self, request={}, params={}):
        self.ensure_one()
        response = VoiceResponse()
        self.get_voicetel_greeting_message(response)
        api_url = self.env['connect.settings'].sudo().get_param('api_url')
        status_url = urljoin(api_url, 'voicetel/webhook/callstatus')
        record_status_url = urljoin(api_url, 'voicetel/webhook/recordingstatus')
        dial_kwargs = {'timeout': self.voicetel_client_ring_timeout}
        if self.record_calls:
            dial_kwargs.update({
                'recordingStatusCallback': record_status_url,
                'record': 'record-from-answer-dual',
            })
        dial = Dial(**dial_kwargs)
        dial.sip(
            'sip:{}'.format(self.voicetel_uri),
            statusCallbackEvent='initiated answered completed',
            statusCallback=status_url,
        )
        response.append(dial)
        return response.to_xml()

    # --- Softphone token (SIP.js over the VoiceTel WSS registrar) ---

    @api.model
    def get_client_token(self, nonce=False):
        """The browser phone's VoiceTel SIP credential for a VoiceTel user."""
        if self._get_phone_provider() != 'voicetel':
            return super().get_client_token(nonce)
        user = self.search([('user', '=', self.env.user.id)], limit=1)
        if not (user.voicetel_client_enabled and user.voicetel_username and user.voicetel_domain):
            return {'token': False}
        return user._get_voicetel_client_token(nonce)

    def _get_voicetel_client_token(self, nonce=False):
        """Mint (or rotate) this browser tab's VoiceTel SIP credential.

        A VoiceTel SIP password has no built-in expiry, unlike a Twilio JWT,
        so re-minting it on every call is what keeps it comparably
        short-lived. The credential is deliberately distinct from the
        desk-phone one (self.voicetel_sid) so rotation never disturbs an
        already-registered desk phone under the same connect.user.

        `nonce` identifies the calling browser tab/session. Each tab gets
        its own credential row keyed on a hash of that nonce, so concurrent
        tabs rotate independent passwords instead of one shared password
        each tab re-registers against.
        """
        self.ensure_one()
        if not (self.voicetel_sip_enabled and self.voicetel_domain):
            logger.info(
                'VoiceTel browser phone requested but SIP is not configured '
                'for user %s.', self.id)
            return {'token': False}
        wss_url = self.env['connect.settings'].sudo().get_param('voicetel_wss_url')
        if not wss_url:
            logger.info(
                'VoiceTel WebRTC WSS URL is not configured — '
                'web phone disabled for user %s.', self.id)
            return {'token': False}
        client = self.env['connect.settings']._voicetel_client()
        if not client:
            logger.info(
                'VoiceTel REST credentials not configured — '
                'web phone disabled for user %s.', self.id)
            return {'token': False}
        nonce_key = hashlib.sha1(nonce.encode()).hexdigest()[:10] if nonce else 'default'
        username = '{}-web-{}'.format(self.voicetel_username, nonce_key)
        password = self.generate_voicetel_password()
        Credential = self.env['connect.voicetel.browser_credential'].sudo()
        existing = Credential.search([('user', '=', self.id), ('nonce', '=', nonce_key)], limit=1)
        if existing:
            try:
                client.sip.credential_lists(self.voicetel_domain.cred_list_sid).credentials(
                    existing.sid).update(password=password)
            except Exception as e:
                if 'not found' in str(e):
                    sid = self._create_sip_account(username, password, client=client)
                    existing.write({'sid': sid})
                else:
                    raise ValidationError(format_connect_response(e))
        else:
            sid = self._create_sip_account(username, password, client=client)
            Credential.create({'user': self.id, 'nonce': nonce_key, 'sid': sid})
            self._prune_browser_credentials(client)
        return {
            'provider': 'voicetel',
            'sip_uri': self.voicetel_uri,
            'username': username,
            # Plaintext SIP password reaches the browser here — same trust
            # boundary as a JWT (authenticated ORM channel), no worse an
            # exposure than a bearer token, but noted since this credential
            # (unlike a JWT) has no built-in expiry on its own.
            'password': password,
            'wss_server': wss_url,
            'display_name': self.name or self.voicetel_username,
        }

    def _prune_browser_credentials(self, client):
        """Evict least-recently-rotated VoiceTel browser credentials once a
        user has more than VOICETEL_MAX_BROWSER_CREDENTIALS on file.

        Tabs that are closed without a clean unregister never tell us to
        clean up after them, so without a cap a user who opens many tabs
        over time would accumulate SIP credentials in VoiceTel forever.
        """
        self.ensure_one()
        Credential = self.env['connect.voicetel.browser_credential'].sudo()
        all_creds = Credential.search([('user', '=', self.id)], order='write_date desc')
        stale = all_creds[VOICETEL_MAX_BROWSER_CREDENTIALS:]
        for cred in stale:
            try:
                client.sip.credential_lists(self.voicetel_domain.cred_list_sid).credentials(cred.sid).delete()
            except Exception as e:
                if 'not found' not in str(e):
                    logger.warning(
                        'Failed to delete stale VoiceTel browser credential %s for user %s: %s',
                        cred.sid, self.id, e)
        stale.unlink()
