import logging
import re

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..tools.textable_client import (
    NANP_E164, TextableClient, TextableError, last4)

_logger = logging.getLogger(__name__)

# Credential fields: the real column is readable by ERP managers only, and
# the form edits a display_ twin that is replaced by asterisks on save.
TEXTABLE_SECRET_FIELDS = ('account_uid', 'api_key')

_NUMBER_FORMATTING = re.compile(r'[\s().-]')


def normalize_phone(value):
    """Drop spaces, dots, dashes and parentheses; nothing else is guessed."""
    if not isinstance(value, str):
        return value
    return _NUMBER_FORMATTING.sub('', value)


class TextableNumber(models.Model):
    _name = 'connect.textable.number'
    _description = 'Textable Sender Number'
    _order = 'sequence, id'

    name = fields.Char(
        required=True,
        help='A label people recognize this number by.')
    active = fields.Boolean(
        default=True,
        help='Archived numbers neither send nor accept relay posts.')
    sequence = fields.Integer(
        default=10,
        help='The first assigned number in this order is a user\'s default sender.')
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company,
        help='Company that sends from this number.')
    phone_number = fields.Char(
        required=True, index=True,
        help='The DID in E.164 form, NANP only, for example +13125550123.')
    textable_user_id = fields.Char(
        string='Textable User ID', required=True,
        help='The Textable user that holds this number. Its token is the one '
             'below, and relay posts for this number must carry this id.')
    account_uid = fields.Char(
        groups='base.group_erp_manager', copy=False,
        help='Account UID half of the Textable user token.')
    api_key = fields.Char(
        groups='base.group_erp_manager', copy=False,
        help='API key half of the Textable user token.')
    display_account_uid = fields.Char(
        string='Account UID', copy=False,
        help='Type the account UID here; it is stored apart and shown masked.')
    display_api_key = fields.Char(
        string='API Key', copy=False,
        help='Type the API key here; it is stored apart and shown masked.')
    textable_credentials_set = fields.Boolean(
        string='Credentials Set', compute='_compute_textable_credentials_set',
        compute_sudo=True,
        help='Whether both halves of the Textable token are stored.')
    textable_relay_configured = fields.Boolean(
        string='Relay URL Issued', compute='_compute_textable_relay',
        compute_sudo=True,
        help='Whether the Textable user of this number has a relay webhook '
             'secret. Relay posts for the user are refused until one is rotated in.')
    textable_relay_rotated_at = fields.Datetime(
        string='Relay URL Rotated', compute='_compute_textable_relay',
        compute_sudo=True,
        help='When the relay URL of this number\'s Textable user was last rotated.')
    campaign_state = fields.Selection(
        [('not_approved', 'Not approved'), ('approved', 'Approved')],
        string='10DLC Campaign', required=True, default='not_approved',
        help='Nothing is sent from this number until its 10DLC campaign is '
             'approved.')
    campaign_use_case = fields.Char(
        string='Campaign Use Case',
        help='The registered 10DLC use case, for example "Account notifications".')
    campaign_reference = fields.Char(
        string='Campaign Reference',
        help='The campaign id the registrar issued. Required once approved.')
    user_ids = fields.Many2many(
        'connect.user', 'connect_textable_number_user_rel', 'number_id',
        'user_id', string='Assigned Users',
        help='Connect users who may send from this number.')
    hourly_cap = fields.Integer(
        default=30, required=True,
        help='Most sends from this number in any rolling hour.')
    daily_cap = fields.Integer(
        default=200, required=True,
        help='Most sends from this number in any rolling 24 hours.')
    recipient_daily_cap = fields.Integer(
        default=10, required=True,
        help='Most sends to one recipient from this company\'s Textable '
             'numbers in any rolling 24 hours.')
    optout_hourly_cap = fields.Integer(
        default=20, required=True,
        help='Most STOP/START keyword changes to the phone blacklist that '
             'relay posts for this number may make in any rolling hour.')

    # A DID belongs to one Textable user: it is configured once, in one
    # company, so a send or a relay post never resolves to two records.
    _phone_unique = models.Constraint(
        'UNIQUE(phone_number)',
        'This number is already configured.',
    )
    _caps_positive = models.Constraint(
        'CHECK(hourly_cap > 0 AND daily_cap > 0 AND recipient_daily_cap > 0 '
        'AND optout_hourly_cap > 0)',
        'Send and opt-out caps must be positive.',
    )

    def _compute_textable_credentials_set(self):
        for rec in self:
            rec.textable_credentials_set = bool(rec.account_uid and rec.api_key)

    @api.depends('textable_user_id')
    def _compute_textable_relay(self):
        Relay = self.env['connect.textable.relay'].sudo()
        relays = {
            relay.textable_user_id: relay for relay in Relay.search(
                [('textable_user_id', 'in', list(set(self.mapped('textable_user_id'))))])}
        for rec in self:
            relay = relays.get(rec.textable_user_id, Relay)
            rec.textable_relay_configured = bool(relay.token_hash)
            rec.textable_relay_rotated_at = relay.rotated_at

    @api.constrains('phone_number')
    def _check_phone_number(self):
        for rec in self:
            if not NANP_E164.match(rec.phone_number or ''):
                raise ValidationError(
                    'A Textable number must be a NANP number in E.164 form, '
                    'for example +13125550123.')

    @api.constrains('campaign_state', 'campaign_reference')
    def _check_campaign(self):
        for rec in self:
            if rec.campaign_state == 'approved' and not (rec.campaign_reference or '').strip():
                raise ValidationError(
                    'An approved campaign needs its campaign reference.')

    @staticmethod
    def _textable_take_secrets(vals):
        """Move display_ values into the real fields and mask the display.
        An all-asterisk value is the mask coming back and changes nothing."""
        secret_vals = {}
        for name in TEXTABLE_SECRET_FIELDS:
            display = 'display_' + name
            value = vals.get(display)
            if not value:
                continue
            if set(value) == {'*'}:
                vals.pop(display)
                continue
            secret_vals[name] = value.strip()
            vals[display] = '*' * len(value)
        return secret_vals

    @api.model_create_multi
    def create(self, vals_list):
        secrets_list = []
        for vals in vals_list:
            if 'phone_number' in vals:
                vals['phone_number'] = normalize_phone(vals['phone_number'])
            secrets_list.append(self._textable_take_secrets(vals))
        records = super().create(vals_list)
        for record, secret_vals in zip(records, secrets_list):
            if secret_vals:
                record.sudo().write(secret_vals)
        return records

    def write(self, vals):
        if 'phone_number' in vals:
            vals['phone_number'] = normalize_phone(vals['phone_number'])
        secret_vals = self._textable_take_secrets(vals)
        res = super().write(vals)
        if secret_vals:
            # The display_ write above went through access rules; the
            # protected columns are written as the system on its behalf.
            self.sudo().write(secret_vals)
        return res

    def _textable_client(self):
        """A client for this number's own Textable user."""
        self.ensure_one()
        number = self.sudo()
        settings = self.env['connect.settings']
        try:
            return TextableClient(
                settings._textable_base_url(), number.account_uid or '',
                number.api_key or '')
        except TextableError as exc:
            raise UserError('%s (%s)' % (exc, number.name)) from exc

    def action_textable_test_connection(self):
        """One read-only GET with this number's token. Reports the status
        only."""
        self.ensure_one()
        if not self.env.is_system():
            raise AccessError('Only system administrators can test the Textable connection.')
        result = self._textable_client().probe()
        _logger.info(
            'Textable connection test for number %s (%s): %s',
            self.id, last4(self.phone_number), result.detail)
        ok = result.outcome == 'accepted'
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Textable connection',
                'message': '%s: %s (%s)' % (
                    self.name, 'token accepted' if ok else result.outcome,
                    result.detail),
                'type': 'success' if ok else 'danger',
                'sticky': not ok,
            },
        }

    def action_textable_rotate_relay(self):
        """Issue a new relay URL for the Textable user of this number and
        show it once. It serves every number that user holds."""
        self.ensure_one()
        if not self.env.is_system():
            raise AccessError('Only system administrators can rotate a Textable relay secret.')
        url = self.env['connect.textable.relay']._textable_rotate(self.textable_user_id)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Textable relay URL',
                'message': (
                    'Register this URL as the Relay Webhook of Textable user %s. '
                    'It serves every number that user holds, it is shown once, '
                    'and Odoo keeps only its hash. %s' % (self.textable_user_id, url)),
                'sticky': True,
                'type': 'warning',
            },
        }

    def action_textable_open_test_message(self):
        self.ensure_one()
        if not self.env.is_system():
            raise AccessError('Only system administrators can send a Textable test message.')
        return {
            'type': 'ir.actions.act_window',
            'name': 'Send Textable Test Message',
            'res_model': 'connect.textable.test.message',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_number_id': self.id},
        }
