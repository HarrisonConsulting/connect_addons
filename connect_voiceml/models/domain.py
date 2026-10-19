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

    def create_domain(self, client):
        self.ensure_one()
        domain = client.sip.domains.create(
            domain_name=self.domain_name,
            friendly_name=self.friendly_name or self.domain_name,
            voice_url=self.voice_url,
            voice_method='POST',
            sip_registration=True,
        )
        self.sid = domain.sid
        cred_list = client.sip.credential_lists.create(
            friendly_name=self.domain_name
        )
        self.cred_list_sid = cred_list.sid
        # Bind the credential list for both registration auth and call auth,
        # so SIP endpoints can register AND receive/place calls through it.
        client.sip.domains.auth.registrations.credential_list_mappings(
            self.sid
        ).create(credential_list_sid=self.cred_list_sid)
        client.sip.domains.auth.calls.credential_list_mappings(
            self.sid
        ).create(credential_list_sid=self.cred_list_sid)
        debug(self, 'Created VoiceML SIP domain {}.'.format(self.domain_name))
        return self.sid

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
                except Exception:
                    logger.exception('Failed to delete SIP domain %s', rec.domain_name)
            if rec.cred_list_sid:
                try:
                    client.sip.credential_lists.delete(rec.cred_list_sid)
                except Exception:
                    logger.exception('Failed to delete SIP credential list %s', rec.cred_list_sid)
        return super().unlink()

    @api.model
    def sync(self):
        client = self.env['connect.settings']._voiceml_client()
        for domain in client.sip.domains.list().domains:
            rec = self.search([('domain_name', '=', domain.domain_name)], limit=1)
            if not rec:
                rec = self.with_context(install_mode=True).create({
                    'domain_name': domain.domain_name,
                    'friendly_name': domain.friendly_name,
                })
            if domain.sid:
                rec.sid = domain.sid

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
            caller_id = caller.voiceml_caller_id() if caller else ''
            if not caller_id:
                return ('<Response><Say>You must configure a caller ID number!'
                        '</Say></Response>')
            return settings.get_voiceml_external_call_route(
                '+{}'.format(number), caller_id,
                settings._voiceml_webhook_url('voiceml/webhook/callstatus'))
        return '<Response><Say>Number not found. Goodbye!</Say></Response>'
