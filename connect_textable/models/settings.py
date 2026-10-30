import hashlib
import logging
import secrets

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..tools.textable_client import TextableError, normalize_base_url

_logger = logging.getLogger(__name__)

RELAY_ROUTE_PREFIX = '/textable/webhook/relay/'


class ConnectSettings(models.Model):
    _inherit = 'connect.settings'

    # Not secrets, but they decide where per-number credentials are sent:
    # system administrators only.
    textable_base_url = fields.Char(
        string='Textable Base URL', groups='base.group_system',
        help='The ElectSys Textable tenant, https://<label>.textable.app. '
             'Per-number credentials are sent only to this host; it is '
             'checked again before every request.')
    textable_organization_id = fields.Char(
        string='Textable Organization ID', groups='base.group_system',
        help='TextableOrganizationID that every relay webhook payload must '
             'carry. Relay posts are refused while it is empty.')
    # Only the sha256 of the relay path token is kept. Written with
    # skip_protected_fields and read from the column, never through
    # get_param(), so a stray ir.config_parameter cannot stand in for it.
    textable_relay_token_hash = fields.Char(
        groups='base.group_system', readonly=True, copy=False,
        help='sha256 of the relay webhook path token. The token itself is '
             'shown once, when it is rotated, and never stored.')
    textable_relay_rotated_at = fields.Datetime(
        string='Relay Secret Rotated', groups='base.group_system',
        readonly=True, copy=False,
        help='When the relay webhook secret was last rotated.')
    textable_relay_configured = fields.Boolean(
        string='Relay Secret Set', compute='_compute_textable_relay_configured',
        compute_sudo=True,
        help='Whether a relay webhook secret exists. Relay posts are refused '
             'until one is rotated in.')

    def _compute_textable_relay_configured(self):
        for rec in self:
            rec.textable_relay_configured = bool(rec.sudo().textable_relay_token_hash)

    @api.constrains('textable_base_url')
    def _check_textable_base_url(self):
        for rec in self.sudo():
            if not rec.textable_base_url:
                continue
            try:
                normalize_base_url(rec.textable_base_url)
            except TextableError as exc:
                raise ValidationError(str(exc)) from exc

    @api.model
    def _textable_base_url(self):
        """The validated tenant URL. Checked here and not only on save:
        the value can arrive through ir.config_parameter."""
        raw = self.sudo().get_param('textable_base_url')
        try:
            return normalize_base_url(raw or '')
        except TextableError as exc:
            raise UserError(str(exc)) from exc

    @api.model
    def _textable_organization_id(self):
        return (self.sudo().get_param('textable_organization_id') or '').strip()

    @api.model
    def _textable_settings_record(self):
        return self.sudo().search([], limit=1)

    @api.model
    def _textable_relay_token_hash(self):
        return self._textable_settings_record().textable_relay_token_hash or ''

    @api.model
    def _textable_public_url(self):
        params = self.env['ir.config_parameter'].sudo()
        url = params.get_param('connect.api_url') or params.get_param('web.base.url') or ''
        return url.rstrip('/')

    def rotate_textable_relay_secret(self):
        """Mint a new relay path token, keep only its sha256, and show the
        full relay URL once. The old URL stops working at once."""
        if not self.env.is_system():
            raise AccessError('Only system administrators can rotate the Textable relay secret.')
        base = self._textable_public_url()
        if not base.startswith('https://'):
            raise UserError(
                'The public URL of this database (connect.api_url or '
                'web.base.url) must use https before a relay URL is issued.')
        token = secrets.token_urlsafe(32)
        record = self._textable_settings_record()
        if not record:
            record = self.sudo().with_context(no_constrains=True).create({})
        record.with_context(skip_protected_fields=True, skip_icp_sync=True).sudo().write({
            'textable_relay_token_hash': hashlib.sha256(token.encode()).hexdigest(),
            'textable_relay_rotated_at': fields.Datetime.now(),
        })
        _logger.info('Textable relay secret rotated by user %s', self.env.uid)
        url = base + RELAY_ROUTE_PREFIX + token
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Textable relay URL',
                'message': (
                    'Register this URL as the Relay Webhook of every Textable '
                    'user that holds a configured number. It is shown once; '
                    'Odoo keeps only its hash. %s' % url),
                'sticky': True,
                'type': 'warning',
            },
        }
