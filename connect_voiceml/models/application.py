# -*- coding: utf-8 -*-
import logging
from urllib.parse import urljoin, urlparse

from odoo import fields, models, api

from voiceml.models import CreateApplicationRequest, UpdateApplicationRequest

from odoo.addons.connect.models.settings import debug

from .twiml_builder import TwiMLResponse

logger = logging.getLogger(__name__)

# (model, method) pairs a private (underscore) method may be rendered from.
ALLOWED_APP_METHODS = {('connect.voiceml.domain', '_route_call')}


class Application(models.Model):
    _name = 'connect.voiceml.application'
    _description = 'VoiceML TwiML Application'
    _order = 'name'

    sid = fields.Char('SID', readonly=True)
    name = fields.Char(required=True)
    description = fields.Text()
    code_type = fields.Selection([
        ('twiml', 'TwiML'),
        ('model_method', 'model.method'),
    ], required=True, default='twiml')
    twiml = fields.Text(
        required=True, string='TwiML',
        default='<?xml version="1.0" encoding="UTF-8"?><Response><Say>Hello</Say></Response>'
    )
    model = fields.Char()
    method = fields.Char()
    voice_url = fields.Char(compute='_get_voiceml_urls', compute_sudo=True)
    voice_fallback_url = fields.Char(compute='_get_voiceml_urls', compute_sudo=True)
    voice_status_url = fields.Char(compute='_get_voiceml_urls', compute_sudo=True)
    exten = fields.Many2one('connect.voiceml.exten', ondelete='set null', readonly=True)

    def _get_voiceml_urls(self):
        settings = self.env['connect.settings']
        api_url = settings.get_param('api_url')
        fallback_url = settings.get_param('api_fallback_url')
        for rec in self:
            rec.voice_url = urljoin(api_url, 'voiceml/webhook/application/{}'.format(rec.id))
            rec.voice_status_url = urljoin(api_url, 'voiceml/webhook/callstatus')
            rec.voice_fallback_url = (
                urljoin(fallback_url, 'voiceml/webhook/application/{}'.format(rec.id))
                if fallback_url else ''
            )

    def create_application(self, client):
        self.ensure_one()
        application = client.applications.create(CreateApplicationRequest(
            friendly_name=self.name,
            voice_url=self.voice_url,
            voice_method='POST',
            voice_fallback_url=self.voice_fallback_url,
            status_callback=self.voice_status_url,
        ))
        self.write({'sid': application.sid})
        debug(self, 'Created VoiceML application {}.'.format(self.name))
        return application

    def update_application(self, client):
        self.ensure_one()
        if not self.sid:
            return self.create_application(client)
        return client.applications.update(self.sid, UpdateApplicationRequest(
            friendly_name=self.name,
            voice_url=self.voice_url,
            voice_method='POST',
            voice_fallback_url=self.voice_fallback_url,
            status_callback=self.voice_status_url,
        ))

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get('install_mode') is True:
            return super().create(vals_list)
        client = self.env['connect.settings']._voiceml_client()
        records = super().create(vals_list)
        for rec in records:
            rec.create_application(client)
        return records

    def write(self, vals):
        res = super().write(vals)
        if not self.env["connect.settings"].get_param("voiceml_auto_sync"):
            return res
        client = self.env['connect.settings']._voiceml_client()
        for rec in self:
            if rec.sid:
                rec.update_application(client)
        return res

    def unlink(self):
        client = self.env['connect.settings']._voiceml_client()
        for rec in self:
            if rec.sid:
                try:
                    client.applications.delete(rec.sid)
                except Exception:
                    logger.exception('Failed to delete VoiceML application %s', rec.sid)
        return super().unlink()

    @api.model
    def sync(self):
        """Refresh remote applications that already have a local record.

        Never imports a remote application, and never repoints one whose
        voice_url host is not this instance's api_url host.
        """
        client = self.env['connect.settings']._voiceml_client()
        api_host = urlparse(self.env['connect.settings'].get_param('api_url') or '').hostname
        existing = {rec.sid: rec for rec in self.search([('sid', '!=', False)])}
        for application in client.applications.list().applications:
            rec = existing.get(application.sid)
            if not rec:
                continue
            remote_host = urlparse(getattr(application, 'voice_url', None) or '').hostname
            if remote_host and remote_host != api_host:
                logger.warning(
                    'Skipping VoiceML application %s: voice_url host %s is not %s',
                    application.sid, remote_host, api_host)
                continue
            rec.update_application(client)

    def _reject_twiml(self):
        response = TwiMLResponse()
        response.reject()
        return response.to_string()

    @api.private
    def render(self, request={}, params={}):
        self.ensure_one()
        if self.code_type == 'model_method' and self.model and self.method:
            key = (self.model, self.method)
            if key not in ALLOWED_APP_METHODS and (
                    self.method.startswith('_') or hasattr(models.BaseModel, self.method)):
                # Underscore methods and ORM primitives (unlink, write, ...)
                # are never webhook targets unless allowlisted.
                logger.warning('Refusing to render application %s: %s.%s not allowed',
                               self.id, self.model, self.method)
                return self._reject_twiml()
            if self.model not in self.env or not callable(getattr(self.env[self.model], self.method, None)):
                return self._reject_twiml()
            return getattr(self.env[self.model], self.method)(request, params)
        return self.twiml
