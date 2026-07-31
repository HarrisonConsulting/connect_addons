# -*- coding: utf-8 -*-

import hashlib
import logging

from odoo import fields, models
from odoo.exceptions import ValidationError
from odoo.models import Constraint
from odoo.addons.connect.models.settings import format_connect_response

logger = logging.getLogger(__name__)

# Each open browser tab mints its own VoiceTel SIP credential (see
# VoicetelUser._get_voicetel_client_token) so concurrent tabs don't rotate
# a shared password out from under each other. Bounds how many a single
# connect.user can accumulate from tabs that were never cleanly closed.
VOICETEL_MAX_BROWSER_CREDENTIALS = 5


class UserBrowserCredential(models.Model):
    """One VoiceTel SIP credential per (connect.user, browser tab/session).

    Unlike a Twilio JWT (stateless, so N tabs can share one identity), a
    VoiceTel SIP password is stateful — rotating it invalidates it
    everywhere. Keying one credential per browser-tab nonce lets concurrent
    tabs of the same user register independently instead of racing to
    rotate a single shared password out from under each other.
    """
    _name = 'connect.user_browser_credential'
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

    browser_credential_ids = fields.One2many(
        'connect.user_browser_credential', 'user', readonly=True,
        help="Per-browser-tab VoiceTel SIP credentials, kept separate from "
             "the desk-phone credential (field 'sid') so minting/rotating "
             "one never disturbs an already-registered desk phone.")

    def _mint_client_token(self, nonce=False):
        if self.env['connect.settings'].sudo().get_param('rest_provider') == 'voicetel':
            return self._get_voicetel_client_token(nonce)
        return super()._mint_client_token(nonce)

    def _get_voicetel_client_token(self, nonce=False):
        """Mint (or rotate) this browser tab's VoiceTel SIP credential.

        A VoiceTel SIP password has no built-in expiry, unlike a Twilio JWT,
        so re-minting it on every call is what keeps it comparably
        short-lived. The credential is deliberately distinct from the
        desk-phone one (self.sid) so rotation never disturbs an
        already-registered desk phone under the same connect.user.

        `nonce` identifies the calling browser tab/session (see
        connect/static/src/js/main.js). Each tab gets its own credential
        row keyed on a hash of that nonce, so concurrent tabs rotate
        independent passwords instead of one shared password each tab
        re-registers against. Callers that omit it (none currently do; kept
        defensive) all collapse onto one shared credential, reproducing the
        pre-fix single-credential behaviour for that caller only.
        """
        self.ensure_one()
        if not (self.sip_enabled and self.domain):
            logger.info(
                'VoiceTel browser phone requested but SIP is not configured '
                'for user %s.', self.id)
            return {'token': False}
        client = self.env['connect.settings'].get_client()
        if not client:
            logger.info(
                'VoiceTel REST credentials not configured — '
                'web phone disabled for user %s.', self.id)
            return {'token': False}
        nonce_key = hashlib.sha1(nonce.encode()).hexdigest()[:10] if nonce else 'default'
        username = '{}-web-{}'.format(self.username, nonce_key)
        password = self.generate_twilio_password()
        Credential = self.env['connect.user_browser_credential'].sudo()
        existing = Credential.search([('user', '=', self.id), ('nonce', '=', nonce_key)], limit=1)
        if existing:
            try:
                client.sip.credential_lists(self.domain.cred_list_sid).credentials(
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
            'sip_uri': self.connect_uri or self.uri,
            'username': username,
            # Plaintext SIP password reaches the browser here — same trust
            # boundary as the Twilio JWT below (authenticated ORM channel),
            # no worse an exposure than a bearer token, but noted since this
            # credential (unlike the JWT) has no built-in expiry on its own.
            'password': password,
            # VoiceTel WSS gateways are domain-level isolated (one gateway
            # per SIP domain, port 8443), so the server is derived from the
            # user's own domain rather than configured globally.
            'wss_server': 'wss://{}:8443'.format(self.domain.domain_name),
            'display_name': self.name or self.username,
        }

    def _prune_browser_credentials(self, client):
        """Evict least-recently-rotated VoiceTel browser credentials once a
        user has more than VOICETEL_MAX_BROWSER_CREDENTIALS on file.

        Tabs that are closed without a clean unregister never tell us to
        clean up after them, so without a cap a user who opens many tabs
        over time would accumulate SIP credentials in VoiceTel forever.
        """
        self.ensure_one()
        Credential = self.env['connect.user_browser_credential'].sudo()
        all_creds = Credential.search([('user', '=', self.id)], order='write_date desc')
        stale = all_creds[VOICETEL_MAX_BROWSER_CREDENTIALS:]
        for cred in stale:
            try:
                client.sip.credential_lists(self.domain.cred_list_sid).credentials(cred.sid).delete()
            except Exception as e:
                if 'not found' not in str(e):
                    logger.warning(
                        'Failed to delete stale VoiceTel browser credential %s for user %s: %s',
                        cred.sid, self.id, e)
        stale.unlink()
