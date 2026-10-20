# -*- coding: utf-8 -*-
import logging

from odoo import fields, models, api
from odoo.exceptions import AccessError, ValidationError

from odoo.addons.connect.models.settings import debug
from .number import fetch_all_pages

logger = logging.getLogger(__name__)


class OutgoingCallerId(models.Model):
    _name = 'connect.voiceml.outgoing_callerid'
    _description = 'VoiceML Outgoing Caller ID'
    _rec_name = 'number'
    _order = 'number'

    number = fields.Char(required=True)
    friendly_name = fields.Char()
    status = fields.Char()
    is_default = fields.Boolean()
    sid = fields.Char()
    callerid_type = fields.Selection(
        [('outgoing_callerid', 'Outgoing Caller ID'), ('number', 'Number')],
        default='outgoing_callerid', required=True)

    def _list_ocid(self, client):
        return fetch_all_pages(client.outgoing_caller_ids.list, 'outgoing_caller_ids')

    def _create_validation_request(self, number, friendly_name):
        client = self.env['connect.settings']._voiceml_client()
        return client.outgoing_caller_ids.create(
            phone_number=number, friendly_name=friendly_name)

    def _update_ocid(self, sid, friendly_name):
        client = self.env['connect.settings']._voiceml_client()
        client.outgoing_caller_ids.update(sid, friendly_name=friendly_name)

    def _delete_ocid(self, sid):
        client = self.env['connect.settings']._voiceml_client()
        client.outgoing_caller_ids.delete(sid)

    @api.model
    def _sync_create_savepoint(self, vals):
        try:
            with self.env.cr.savepoint():
                return self.create(vals)
        except Exception:
            logger.exception('Caller ID sync failed for %s', vals.get('sid'))
            return self.browse()

    @api.model
    def sync(self):
        settings = self.env['connect.settings']
        client = settings._voiceml_client()
        # Complete fetches first: a failing page raises before any change.
        remote = self._list_ocid(client)
        numbers = fetch_all_pages(
            client.incoming_phone_numbers.list, 'incoming_phone_numbers')
        seen_sids = set()
        for row in remote:
            sid = row.sid
            if not sid:
                continue
            seen_sids.add(sid)
            rec = self.search([('sid', '=', sid)])
            if not rec:
                self._sync_create_savepoint({
                    'number': row.phone_number,
                    'sid': sid,
                    'friendly_name': row.friendly_name,
                    'status': 'validated',
                    'callerid_type': 'outgoing_callerid',
                })
        # Numbers already synced by connect.voiceml.number are also usable as
        # caller IDs; mirror them here so the dropdown is complete.
        for number in numbers:
            rec = self.search([
                ('callerid_type', '=', 'number'),
                ('number', '=', number.phone_number),
            ])
            if not rec:
                self._sync_create_savepoint({
                    'number': number.phone_number,
                    'sid': number.sid,
                    'friendly_name': number.friendly_name,
                    'callerid_type': 'number',
                })

    def validate(self):
        self.ensure_one()
        if not (self.env.is_superuser() or self.env.user.has_group('connect.group_admin')):
            raise AccessError('Only Connect administrators can validate caller IDs.')
        if self.callerid_type != 'outgoing_callerid':
            raise ValidationError('Only outgoing caller IDs need validation.')
        self._create_validation_request(self.number, self.friendly_name)
        self.status = 'not validated'

    @api.model
    def _update_status(self, params):
        """Outgoing caller-id validation webhook: reflect the new status."""
        number = params.get('PhoneNumber')
        if not number:
            return False
        rec = self.search([('number', '=', number), ('callerid_type', '=', 'outgoing_callerid')], limit=1)
        if rec:
            rec.status = params.get('VerificationStatus') or params.get('Status') or rec.status
        return True

    def unlink(self):
        for rec in self:
            if rec.callerid_type == 'outgoing_callerid' and rec.sid:
                try:
                    rec._delete_ocid(rec.sid)
                except Exception:
                    logger.exception('Could not delete outgoing caller id %s', rec.number)
        return super().unlink()
