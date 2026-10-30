import hashlib
import hmac
import logging
import secrets

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

RELAY_ROUTE_PREFIX = '/textable/webhook/relay/'


def relay_token_hash(token):
    return hashlib.sha256((token or '').encode()).hexdigest()


class TextableRelay(models.Model):
    """One relay webhook secret per Textable user.

    Textable POSTs to the relayWebhook of the user a message is sent or
    received for (spec 5.29.16, x-webhooks.relayWebhook, spec.json:2862-2865),
    so every Textable user has its own URL. Only the sha256 of its path
    token is kept, and a post is accepted only for the Textable user its
    token was issued to: one user's URL cannot speak for another's numbers.
    """
    _name = 'connect.textable.relay'
    _description = 'Textable Relay Webhook Secret'
    _order = 'textable_user_id'
    _rec_name = 'textable_user_id'

    textable_user_id = fields.Char(
        string='Textable User ID', required=True, readonly=True, index=True,
        help='The Textable user whose Relay Webhook carries this secret.')
    token_hash = fields.Char(
        groups='base.group_system', readonly=True, copy=False,
        help='sha256 of the relay path token. The token itself is shown once, '
             'when it is rotated, and never stored.')
    rotated_at = fields.Datetime(
        string='Rotated', readonly=True, copy=False,
        help='When this relay secret was last rotated.')

    _textable_user_unique = models.Constraint(
        'UNIQUE(textable_user_id)',
        'This Textable user already has a relay secret.',
    )

    @api.model
    def _textable_match_token(self, token):
        """Return (whether any secret is configured, the relay whose hash
        matches ``token``). Every configured hash is compared in constant
        time; the token is never looked up by value."""
        presented = relay_token_hash(token)
        relays = self.sudo().search([('token_hash', '!=', False)])
        match = self.browse()
        for relay in relays:
            if hmac.compare_digest(presented, relay.token_hash):
                match = relay
        return bool(relays), match

    @api.model
    def _textable_rotate(self, textable_user_id):
        """Mint a new path token for one Textable user, keep only its
        sha256, and return the full relay URL. The old URL of that user stops
        working at once; other users' URLs are untouched."""
        if not self.env.is_system():
            raise AccessError('Only system administrators can rotate a Textable relay secret.')
        textable_user_id = (textable_user_id or '').strip()
        if not textable_user_id:
            raise UserError('The number has no Textable user id.')
        base = self.env['connect.settings']._textable_public_url()
        if not base.startswith('https://'):
            raise UserError(
                'The public URL of this database (connect.api_url or '
                'web.base.url) must use https before a relay URL is issued.')
        token = secrets.token_urlsafe(32)
        Relay = self.sudo()
        relay = Relay.search([('textable_user_id', '=', textable_user_id)], limit=1)
        if not relay:
            relay = Relay.create({'textable_user_id': textable_user_id})
        relay.write({
            'token_hash': relay_token_hash(token),
            'rotated_at': fields.Datetime.now(),
        })
        _logger.info(
            'Textable relay secret of Textable user %s rotated by user %s',
            textable_user_id, self.env.uid)
        return base + RELAY_ROUTE_PREFIX + token
