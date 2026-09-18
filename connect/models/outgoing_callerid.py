# -*- coding: utf-8 -*-

import logging
import re
from urllib.parse import urljoin
from odoo import fields, models, api, release
from odoo.models import Constraint
from odoo.exceptions import ValidationError
from .settings import debug, format_connect_response

logger = logging.getLogger(__name__)


class OutgoingCallerID(models.Model):
    _name = 'connect.outgoing_callerid'
    _description = 'Outgoing CallerId'
    _order = 'number'

    name = fields.Char(compute='_get_name')
    sid = fields.Char(readonly=True)
    friendly_name = fields.Char(required=True)
    number = fields.Char(required=True)
    status = fields.Char(readonly=True)
    validation_code = fields.Char(readonly=True)
    callerid_type = fields.Selection([('outgoing_callerid', 'CallerID'), ('number', 'DID Number')],
                                     required=True, default='outgoing_callerid')
    is_default = fields.Boolean(string='Default')
    callerid_users = fields.One2many(comodel_name='connect.user',
                                     inverse_name='outgoing_callerid', string='callerId Users')

    _number_uniq = Constraint('UNIQUE(number)', 'This number is already used!')

    def _get_name(self):
        for rec in self:
            rec.name = '{} "{}"'.format(rec.number, rec.friendly_name)

    def sync_outgoing_callerid(self, callerid_type):
        client = self.env['connect.settings'].get_client(region=False)
        if callerid_type == 'outgoing_callerid':
            numbers = client.outgoing_caller_ids.list()
        elif callerid_type == 'number':
            numbers = client.incoming_phone_numbers.list()
        else:
            numbers = []
        # First get numbers from Twilio.
        sid_conflicts = []
        for number in numbers:
            existing_number = self.env['connect.outgoing_callerid'].search([
                ('sid', '=', number.sid)])
            if existing_number and existing_number.number != number.phone_number:
                # The provider paired a SID we already map to one number with
                # a different number, i.e. it recycled the SID (task 9594;
                # Twilio never does). Writing it would move this row onto
                # digits another row may hold and trip UNIQUE(number) --
                # reported at the NEXT search(), when Odoo flushes the write,
                # which makes the traceback point at the wrong line. Skip it
                # and say so rather than prefer either side.
                settings = self.env['connect.settings']
                logger.warning(
                    'Caller ID sync skipped %s: provider returned it for %s, '
                    'Odoo holds it for %s [%s]',
                    settings._short_sid(number.sid), number.phone_number,
                    existing_number.number, settings._provider_log_context())
                sid_conflicts.append('{} (Odoo: {})'.format(
                    number.phone_number, existing_number.number))
                continue
            if not existing_number:
                existing_number = self.env['connect.outgoing_callerid'].search([
                    ('number', '=', number.phone_number)])
            data = {
                    'sid': number.sid,
                    'callerid_type': callerid_type,
                    'number': number.phone_number,
                    # Twilio always sends a friendly name; Twilio-compatible
                    # providers may send null. friendly_name is NOT NULL
                    # locally, so a missing value must not abort the sync.
                    'friendly_name': number.friendly_name or number.phone_number,
            }
            callerid_count = self.search_count([])
            if callerid_count == 0:
                data['is_default'] = True
            if callerid_type == 'outgoing_callerid':
                data['status'] = 'validated'
            if not existing_number:
                # New number added, create record in Odoo.
                self.with_context(skip_validation=True).create(data)
                debug(self, 'CallerID {} ({}) created in Odoo from {}'.format(
                    number.phone_number, number.friendly_name, callerid_type))
            else:
                if not number.friendly_name:
                    # Provider has no friendly name for this number: keep
                    # the locally set one instead of clobbering it with the
                    # phone-number fallback on every sync.
                    data.pop('friendly_name')
                # CallerID exists, update it's type.
                existing_number.with_context(skip_number_check=True).write(data)
                # CallerID exists, update friendly name to Twilio
                if number.friendly_name != existing_number.friendly_name:
                    debug(self, 'Update CallerID {} friendly name.'.format(existing_number.number))
                    if callerid_type == 'outgoing_callerid':
                        client.outgoing_caller_ids(existing_number.sid).update(
                            friendly_name=existing_number.friendly_name)
                    else:
                        client.incoming_phone_numbers(existing_number.sid).update(
                            friendly_name=existing_number.friendly_name)
        if sid_conflicts:
            provider = self.env['connect.settings']._provider_label()
            self.env['connect.settings'].connect_notify(
                title='Sync skipped caller IDs',
                message='{} reused an identifier Odoo already maps to a '
                        'different number, so these were not updated: {}. '
                        'Ask {} whether phone-number SIDs are reused.'.format(
                            provider, ', '.join(sid_conflicts), provider),
                warning=True, sticky=True)
        # Now sync numbers from Odoo. A removal that would drop most of what
        # we hold is refused: a partial or empty listing is far more likely
        # a mid-migration state, a compat-provider gap or the wrong account
        # than those caller IDs having been released, and the alternative
        # wipes defaults and user assignments on a single SYNC click (same
        # guard as connect.number).
        recs_to_remove = self.env['connect.outgoing_callerid'].search(
            [('sid', 'not in', [k.sid for k in numbers]),
             # Match by number too, so an account swap re-adopts records
             # instead of deleting every row whose SID no longer resolves.
             ('number', 'not in', [k.phone_number for k in numbers]),
             ('callerid_type', '=', callerid_type)])
        if self.env['connect.settings']._refuse_implausible_removal(
                'caller IDs of type {}'.format(callerid_type), len(numbers),
                self.search_count([('callerid_type', '=', callerid_type)]),
                recs_to_remove.mapped('number')):
            return
        debug(self, 'Removing {} CallerIds: {}'.format(callerid_type, [k.number for k in recs_to_remove]))
        # The removal was judged plausible above, so it may pass the
        # interlock in unlink() that refuses a manual delete.
        recs_to_remove.with_context(connect_sync_removal=True).unlink()

    @api.model
    def sync(self):
        self.sync_outgoing_callerid('outgoing_callerid')
        self.sync_outgoing_callerid('number')

    @api.model
    def update_status(self, params):
        self = self.sudo()
        number = self.search([('number', '=', params['Called']),
                              ('callerid_type', '=', 'outgoing_callerid')])
        if not number:
            logger.error('Unknown validation request for number %s', params['Called'])
            return False
        if params['VerificationStatus'] == 'success':
            number.write({'status': 'validated', 'sid': params['OutgoingCallerIdSid']})
        else:
            number.status = 'validation failed'
        self.env['connect.settings'].connect_reload_view('connect.outgoing_callerid')
        return True

    def validate(self):
        self.ensure_one()
        if (self.env['connect.settings'].sudo().get_param('twilio_region') != 'us1'
                and not self.env['connect.settings'].uses_compatible_rest_api()):
            raise ValidationError('Outgoing CallerIds are supported in US1 region only!')
        if self.sid:
            raise ValidationError('Outgoing callerid is already validated!')
        api_url = self.env['connect.settings'].sudo().get_param('api_url')
        edge = self.env['connect.settings'].get_param('twilio_edge')
        status_url = urljoin(api_url, 'twilio/webhook/outgoing_callerid#e={}'.format(edge))
        client = self.env['connect.settings'].get_client()
        try:
            validation_request = client.validation_requests.create(
                status_callback=status_url,
                friendly_name=self.friendly_name, phone_number=self.number)
            self.validation_code = validation_request.validation_code
        except Exception as e:
            if 'Phone number is already verified.' in str(e):
                # Remove number and sync
                self.unlink()
                self.sync()
                return {
                    'type': 'ir.actions.act_window',
                    'res_model': 'connect.outgoing_callerid',
                    'view_mode': 'tree' if release.version_info[0] < 18 else 'list',
                    'name': 'Outgoing CallerIds',
                }
            else:
                logger.error('Validate request error: %s', e)
                raise ValidationError('Validate request error: {}'.format(format_connect_response(e)))

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get('skip_validation'):
            return super().create(vals_list)
        for vals in vals_list:
            if vals['callerid_type'] == 'outgoing_callerid':
                vals['status'] = 'not validated'
        return super().create(vals_list)

    @api.constrains('friendly_name')
    def _change_number_friendly_name(self):
        for rec in self:
            if rec.sid and rec.callerid_type == 'outgoing_callerid':
                # Check if twilio_auto_sync is disabled
                if self.env["connect.settings"].get_param("twilio_auto_sync"):
                    client = self.env['connect.settings'].get_client(region=False)
                    client.outgoing_caller_ids(rec.sid).update(friendly_name=self.friendly_name)
            elif rec.sid and rec.callerid_type == 'number':
                # Sync number friendly name.
                number = self.env['connect.number'].search([('phone_number', '=', rec.number)])
                number.friendly_name = rec.friendly_name

    def unlink(self):
        if not self.env["connect.settings"].get_param("twilio_auto_sync"):
            # With auto sync off Odoo does not mirror the provider's
            # inventory, so local caller IDs are the operator's to manage.
            # That is also the documented way out of the refusal below.
            return super().unlink()
        sids = {}
        for rec in self:
            if rec.callerid_type == 'number' and not self.env.context.get('connect_sync_removal'):
                # A safety interlock, not a workflow hint: it is what stopped
                # the 2026-09-11 sync from deleting live caller IDs (task
                # 9591). A number-type caller ID mirrors a phone number on
                # the provider account, so only the sync may remove it, and
                # only after _refuse_implausible_removal() has passed it.
                provider = self.env['connect.settings']._provider_label()
                raise ValidationError(
                    'Odoo will not delete the caller ID for {number}: it is a '
                    'phone number on your {provider} account, and deleting it '
                    'here would leave Odoo out of step with {provider}. '
                    'Release the number at {provider}, then run Sync, which '
                    'removes it. To manage caller IDs by hand instead, turn '
                    'off Auto Sync in Connect settings.'.format(
                        number=rec.number, provider=provider))
            if rec.sid and rec.callerid_type == 'outgoing_callerid':
                sids[rec.sid] = rec.number
        res = super().unlink()
        client = self.env['connect.settings'].get_client(region=False)
        for sid in sids.keys():
            try:
                client.outgoing_caller_ids(sid).delete()
            except Exception as e:
                if 'not found' in str(e).lower():
                    # Already absent on the provider: the delete is
                    # idempotent, local removal may proceed.
                    logger.info('Outgoing callerid %s already absent on provider.', sids[sid])
                else:
                    # Anything else must abort so local and provider state
                    # cannot diverge (local record gone, remote kept).
                    logger.error('Could not delete outgoing callerid number %s: %s', sids[sid], e)
                    raise
        return res

    @api.constrains('number')
    def _check_number(self):
        if self.number and not self.number.startswith('+'):
            raise ValidationError('Number must start with +')
        if self.number and not re.search(r'^\+[0-9]+$', self.number):
            raise ValidationError('Number must contain only digits!')

    @api.constrains('is_default')
    def _reset_default(self):
        for rec in self:
            if not self.env.context.get('skip_reset_default'):
                context = {
                    'skip_reset_default': True,
                }
                default = rec.is_default
                # Reset all defaults.
                self.with_context(context).search(
                    []).write({'is_default': False})
                # Set back the default to current.
                rec.with_context(context).is_default = default

    @api.constrains('is_default')
    def _check_default(self):
        for rec in self:
            if rec.is_default:
                if rec.callerid_type == 'outgoing_callerid' and rec.status != 'validated':
                    raise ValidationError('Validate the number first!')
