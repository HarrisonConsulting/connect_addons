from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..tools.textable_client import TextableError, normalize_base_url


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
    def _textable_public_url(self):
        params = self.env['ir.config_parameter'].sudo()
        url = params.get_param('connect.api_url') or params.get_param('web.base.url') or ''
        return url.rstrip('/')
