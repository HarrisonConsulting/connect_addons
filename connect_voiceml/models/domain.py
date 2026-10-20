# -*- coding: utf-8 -*-
import logging
from urllib.parse import urljoin

from odoo import fields, models, api
from odoo.exceptions import ValidationError

from odoo.addons.connect.models.settings import debug
from .settings import MAX_EXTEN_LEN, strip_number

logger = logging.getLogger(__name__)


class Domain(models.Model):
    _name = 'connect.voiceml.domain'
    _description = 'VoiceML SIP Domain'
    _rec_name = 'domain_name'
    _order = 'domain_name'

    domain_name = fields.Char(required=True)
    subdomain = fields.Char(compute='_get_subdomain', store=True)
    friendly_name = fields.Char()
    sid = fields.Char(readonly=True)
    cred_list_sid = fields.Char(readonly=True)
    voice_url = fields.Char(compute='_get_voiceml_urls', compute_sudo=True)

    @api.depends('domain_name')
    def _get_subdomain(self):
        for rec in self:
            rec.subdomain = (rec.domain_name or '').split('.')[0]

    def _get_voiceml_urls(self):
        api_url = self.env['connect.settings'].get_param('api_url')
        for rec in self:
            rec.voice_url = urljoin(api_url, 'voiceml/webhook/domain')

    # --- Remote adoption (VoiceML domain names are globally unique) ---

    @staticmethod
    def _voiceml_is_conflict(error):
        """409, or the Twilio-compatible 21631 duplicate-resource code."""
        return (getattr(error, 'status_code', None) == 409
                or str(getattr(error, 'code', '')) == '21631')

    @staticmethod
    def _voiceml_is_not_found(error):
        return getattr(error, 'status_code', None) == 404

    @staticmethod
    def _voiceml_items(list_fn, attr, max_pages=100):
        """All items of a paged VoiceML list. list_fn is the resource's
        list callable; pages are read until next_page_uri is empty."""
        items = []
        for page in range(max_pages):
            try:
                result = list_fn(page=page, page_size=1000)
            except TypeError:
                result = list_fn() if page == 0 else None
            if result is None:
                break
            chunk = getattr(result, attr, None)
            if chunk is None and isinstance(result, (list, tuple)):
                chunk = result
            items.extend(chunk or [])
            if not getattr(result, 'next_page_uri', None):
                break
        return items

    def _find_remote_domain(self, client):
        self.ensure_one()
        wanted = (self.domain_name or '').lower()
        for remote in self._voiceml_items(client.sip.domains.list, 'domains'):
            if (remote.domain_name or '').lower() == wanted:
                return remote
        return None

    def _mapping_list_sids(self, mappings):
        return [m.credential_list_sid if getattr(m, 'credential_list_sid', None) else m.sid
                for m in mappings]

    def _ensure_cred_list(self, client):
        """Make sure the domain has a credential list mapped for registration
        and calls, reusing the one already mapped on the remote domain."""
        self.ensure_one()
        if not self.sid:
            return self.cred_list_sid
        reg = client.sip.domains.auth.registrations.credential_list_mappings(self.sid)
        reg_sids = self._mapping_list_sids(
            self._voiceml_items(reg.list, 'credential_list_mappings'))
        if reg_sids:
            if self.cred_list_sid not in reg_sids:
                self.cred_list_sid = reg_sids[0]
        else:
            if not self.cred_list_sid:
                cred_list = client.sip.credential_lists.create(
                    friendly_name=self.domain_name)
                self.cred_list_sid = cred_list.sid
            reg.create(credential_list_sid=self.cred_list_sid)
        calls = client.sip.domains.auth.calls.credential_list_mappings(self.sid)
        call_sids = self._mapping_list_sids(
            self._voiceml_items(calls.list, 'credential_list_mappings'))
        if self.cred_list_sid not in call_sids:
            calls.create(credential_list_sid=self.cred_list_sid)
        return self.cred_list_sid

    def _adopt_remote(self, client, remote):
        """Take over an existing remote domain: sid, credential list and the
        voice URL. No write is sent when the remote already matches."""
        self.ensure_one()
        self.sid = remote.sid
        self._ensure_cred_list(client)
        if (getattr(remote, 'voice_url', None) != self.voice_url
                or (getattr(remote, 'voice_method', None) or 'POST').upper() != 'POST'):
            client.sip.domains.update(
                self.sid, voice_url=self.voice_url, voice_method='POST')
        debug(self, 'Adopted VoiceML SIP domain {}.'.format(self.domain_name))
        return self.sid

    def create_domain(self, client):
        self.ensure_one()
        remote = self._find_remote_domain(client)
        if not remote:
            try:
                remote = client.sip.domains.create(
                    domain_name=self.domain_name,
                    friendly_name=self.friendly_name or self.domain_name,
                    voice_url=self.voice_url,
                    voice_method='POST',
                    sip_registration=True,
                )
                # Record the sid at once so a later failure cannot orphan it.
                self.sid = remote.sid
                self._ensure_cred_list(client)
                debug(self, 'Created VoiceML SIP domain {}.'.format(self.domain_name))
                return self.sid
            except Exception as e:
                if not self._voiceml_is_conflict(e):
                    raise
                remote = self._find_remote_domain(client)
                if not remote:
                    raise
        return self._adopt_remote(client, remote)

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get('install_mode') is True:
            return super().create(vals_list)
        client = self.env['connect.settings']._voiceml_client()
        records = super().create(vals_list)
        for rec in records:
            rec.create_domain(client)
        return records

    def unlink(self):
        client = self.env['connect.settings']._voiceml_client()
        for rec in self:
            if rec.sid:
                try:
                    client.sip.domains.delete(rec.sid)
                except Exception as e:
                    if not self._voiceml_is_not_found(e):
                        raise
                    logger.info('SIP domain %s already gone', rec.domain_name)
            if rec.cred_list_sid:
                try:
                    client.sip.credential_lists.delete(rec.cred_list_sid)
                except Exception as e:
                    if not self._voiceml_is_not_found(e):
                        raise
                    logger.info('SIP credential list %s already gone', rec.cred_list_sid)
        return super().unlink()

    @api.model
    def sync(self):
        client = self.env['connect.settings']._voiceml_client()
        for domain in client.sip.domains.list().domains:
            # One savepoint per remote domain: a later failure must not roll
            # back rows whose remote objects already exist.
            try:
                with self.env.cr.savepoint():
                    rec = self.search([('domain_name', '=', domain.domain_name)], limit=1)
                    if not rec:
                        rec = self.with_context(install_mode=True).create({
                            'domain_name': domain.domain_name,
                            'friendly_name': domain.friendly_name,
                        })
                    if domain.sid:
                        rec._adopt_remote(client, domain)
            except Exception:
                logger.exception('Domain sync failed for %s', domain.domain_name)

    @api.model
    def _route_call(self, request, params={}):
        """Inbound call from a SIP or web phone on a VoiceML SIP domain.

        The controller and the seeded SIP-domain application call this on
        an empty recordset, so the domain is resolved from the To host.
        The To user part is a VoiceML username, an extension number, or a
        PSTN number that is dialled out with the caller's caller ID.
        """
        debug(self, 'Route domain call: {}'.format(request))
        # The dialled target arrives in To: sip:<user>@<domain>.
        to = request.get('To', '')
        uri_user, _sep, host = to.split(':', 1)[-1].partition('@')
        host = host.split(';')[0].split(':')[0].lower()
        if not (uri_user and host and self.sudo().search(
                [('domain_name', '=ilike', host)], limit=1)):
            return '<Response><Say>Number not found. Goodbye!</Say></Response>'
        user = self.env['connect.user'].sudo().search(
            [('voiceml_username', '=', uri_user)], limit=1)
        if user:
            return user.render_voiceml(request, params)
        exten = self.env['connect.voiceml.exten'].sudo().search(
            [('number', '=', uri_user)], limit=1)
        if exten:
            return exten.render(request=request, params=params)
        number = strip_number(uri_user)
        if number.isdigit() and len(number) > MAX_EXTEN_LEN:
            settings = self.env['connect.settings'].sudo()
            caller = self.env['connect.user'].sudo().get_user_by_uri(request.get('From'))
            caller_id = caller.voiceml_pstn_caller_id() if caller else ''
            if not caller_id:
                return ('<Response><Say>You must configure a caller ID number!'
                        '</Say></Response>')
            return settings.get_voiceml_external_call_route(
                settings._voiceml_dial_number(number), caller_id,
                settings._voiceml_webhook_url('voiceml/webhook/callstatus'))
        return '<Response><Say>Number not found. Goodbye!</Say></Response>'

    @api.private
    def _revoke_orphan_credentials(self, apply=False):
        """List, and with apply=True delete, SIP credentials on each domain's
        credential list whose username belongs to no active connect.user
        (archived, deleted or SIP disabled) on that domain. Dry run by
        default. Returns and logs a ledger: one dict per credential with
        domain, username, sid, action (would_delete, deleted, already_gone,
        failed, list_failed) and error. One failure never stops the rest."""
        client = self.env['connect.settings']._voiceml_client()
        domains = (self or self.sudo().search([])).sudo()
        ledger = []
        for domain in domains:
            if not domain.cred_list_sid:
                continue
            users = self.env['connect.user'].sudo().with_context(active_test=False).search([
                ('voiceml_domain', '=', domain.id)])
            live = {
                (u.voiceml_username or '').lower() for u in users
                if u.active and u.voiceml_sip_enabled and u.voiceml_username}
            credentials = client.sip.credential_lists.credentials(domain.cred_list_sid)
            try:
                remote = self._voiceml_items(credentials.list, 'credentials')
            except Exception as e:
                ledger.append({'domain': domain.domain_name, 'username': '', 'sid': '',
                               'action': 'list_failed', 'error': str(e)})
                continue
            for cred in remote:
                if (cred.username or '').lower() in live:
                    continue
                row = {'domain': domain.domain_name, 'username': cred.username,
                       'sid': cred.sid, 'action': 'would_delete', 'error': ''}
                if apply:
                    try:
                        credentials.delete(cred.sid)
                        row['action'] = 'deleted'
                    except Exception as e:
                        if self._voiceml_is_not_found(e):
                            row['action'] = 'already_gone'
                        else:
                            row['action'] = 'failed'
                            row['error'] = str(e)
                ledger.append(row)
        for row in ledger:
            logger.info('Orphan SIP credential ledger: %s %s %s %s %s', row['domain'],
                        row['username'], row['sid'], row['action'], row['error'])
        return ledger
