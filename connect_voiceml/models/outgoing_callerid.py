# -*- coding: utf-8 -*-
import logging

import httpx

from odoo import fields, models, api
from odoo.exceptions import AccessError, ValidationError

from odoo.addons.connect.models.settings import debug
from .number import fetch_all_pages
from .settings import VOICEML_DEFAULT_BASE_URL, format_connect_response

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

    def _raw_client(self):
        """httpx client for the OutgoingCallerIds REST surface.

        The VoiceML SDK does not yet wrap OutgoingCallerIds /
        ValidationRequests, so this module talks to the VoiceML API directly
        until the SDK exposes them (tracked as a follow-up). Auth is the same
        HTTP Basic (sid:api_key) the SDK uses everywhere else.
        """
        settings = self.env['connect.settings'].sudo()
        base = (settings.get_param('voiceml_base_url') or VOICEML_DEFAULT_BASE_URL).rstrip('/')
        account_sid = settings.get_param('voiceml_account_sid')
        api_key = settings.get_param('voiceml_api_key')
        if not (base and account_sid and api_key):
            raise ValidationError('Set Account SID and API key!')
        return httpx.Client(
            auth=(account_sid, api_key),
            base_url=base,
            timeout=30,
        ), account_sid

    def _ocid_url(self, account_sid, sid=None):
        url = '/2010-04-01/Accounts/{}/OutgoingCallerIds'.format(account_sid)
        if sid:
            url += '/{}'.format(sid)
        return url + '.json'

    def _list_ocid(self):
        client, account_sid = self._raw_client()
        try:
            rows = []
            url = self._ocid_url(account_sid)
            seen = set()
            while url:
                if url in seen:
                    raise ValidationError('VoiceML paging loop detected at %s' % url)
                seen.add(url)
                resp = client.get(url)
                resp.raise_for_status()
                data = resp.json()
                rows.extend(data.get('outgoing_caller_ids', []))
                url = data.get('next_page_uri')
            return rows
        finally:
            client.close()

    def _create_validation_request(self, number, friendly_name):
        client, account_sid = self._raw_client()
        try:
            resp = client.post(self._ocid_url(account_sid), data={
                'PhoneNumber': number,
                'FriendlyName': friendly_name,
            })
            resp.raise_for_status()
            return resp.json()
        finally:
            client.close()

    def _update_ocid(self, sid, friendly_name):
        client, account_sid = self._raw_client()
        try:
            resp = client.post(self._ocid_url(account_sid, sid), data={
                'FriendlyName': friendly_name,
            })
            resp.raise_for_status()
        finally:
            client.close()

    def _delete_ocid(self, sid):
        client, account_sid = self._raw_client()
        try:
            resp = client.delete(self._ocid_url(account_sid, sid))
            resp.raise_for_status()
        finally:
            client.close()

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
        remote = self._list_ocid()
        numbers = fetch_all_pages(
            client.incoming_phone_numbers.list, 'incoming_phone_numbers')
        seen_sids = set()
        for row in remote:
            sid = row.get('sid')
            if not sid:
                continue
            seen_sids.add(sid)
            rec = self.search([('sid', '=', sid)])
            if not rec:
                self._sync_create_savepoint({
                    'number': row.get('phone_number'),
                    'sid': sid,
                    'friendly_name': row.get('friendly_name'),
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
