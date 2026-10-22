# -*- coding: utf-8 -*-
import logging
from urllib.parse import urlparse
from xml.dom.minidom import parseString

from jinja2.sandbox import SandboxedEnvironment
from voiceml.exceptions import NotFoundError
from voiceml.models import CreateApplicationRequest, UpdateApplicationRequest

from odoo import fields, models, api
from odoo.exceptions import ValidationError
from odoo.addons.connect.models.settings import debug

from .voiceml_response import VoiceResponse

logger = logging.getLogger(__name__)

# The only model methods a 'model.method' application may dispatch to.
# An application row is admin data, but its render runs for every call on
# a public webhook: an open getattr() would let it reach unlink(), write()
# or any other method of any model.
ALLOWED_APP_METHODS = {('connect.voicetel.domain', 'route_call')}

REJECT = '<?xml version="1.0" encoding="UTF-8"?><Response><Reject/></Response>'


def render_template(source, ctx, autoescape=True):
    """Render an admin- or user-written VoiceML/text template.

    Sandboxed: no attribute walk to the ORM internals, no Python. XML
    templates are autoescaped, because the context carries webhook
    parameters that must not inject verbs; a plain-text prompt that the
    VoiceML builder escapes itself is rendered with autoescape=False.
    """
    environment = SandboxedEnvironment(autoescape=autoescape)
    return environment.from_string(source or '').render(dict(ctx))


def pretty_xml(content):
    try:
        dom = parseString(str(content))
        pretty_content = dom.toprettyxml()
        return "\n".join([line for line in pretty_content.splitlines() if line.strip()])
    except Exception as e:
        logger.error('Pretty XML parse error: %s', e)
        return 'Pretty XML parse error: {}\n{}'.format(e, str(content))


class VoiceML(models.Model):
    """A VoiceML application: the VoiceTel counterpart of a TwiML app.

    Each record is mirrored as an Application resource on the VoiceML
    account; its voice URL points back at /voicetel/webhook/voiceml/<id>.
    """
    _name = 'connect.voicetel.voiceml'
    _description = 'VoiceML app'
    _order = 'name'

    sid = fields.Char('SID', readonly=True)
    name = fields.Char(required=True)
    description = fields.Text()
    code_type = fields.Selection([
        ('voiceml', 'VoiceML'),
        ('model_method', 'model.method')
    ], help='VoiceML: a sandboxed template. model.method: one of the '
            'module\'s routing entry points (SIP domain calls).',
        required=True, default='voiceml')
    voiceml = fields.Text(required=True, string='VoiceML',
        default=pretty_xml('<?xml version="1.0" encoding="UTF-8"?><Response><Say>Hello</Say></Response>'))
    model = fields.Char()
    method = fields.Char()
    voice_url = fields.Char(compute='_get_voicetel_urls', compute_sudo=True)
    voice_fallback_url = fields.Char(compute='_get_voicetel_urls', compute_sudo=True)
    voice_status_url = fields.Char(compute='_get_voicetel_urls', compute_sudo=True)
    exten = fields.Many2one('connect.voicetel.exten', ondelete='set null', readonly=True)
    exten_number = fields.Char(related='exten.number')

    @api.private
    def create_voicetel_app(self, client):
        self.ensure_one()
        application = client.applications.create(CreateApplicationRequest(
            VoiceUrl=self.voice_url,
            VoiceMethod='POST',
            VoiceFallbackUrl=self.voice_fallback_url,
            VoiceFallbackMethod='POST',
            FriendlyName=self.name,
            StatusCallback=self.voice_status_url,
            StatusCallbackMethod='POST',
        ))
        self.write({'sid': application.sid})
        debug(self, 'Created VoiceML app {}.'.format(self.name))
        return application

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if self.env.context.get('install_mode') or self.env.context.get(
                'no_voicetel_create'):
            return records
        if not self.env["connect.settings"].get_param("voicetel_auto_sync"):
            return records
        client = self.env['connect.settings'].get_voicetel_client()
        for rec in records:
            rec.create_voicetel_app(client)
        return records

    def write(self, vals):
        res = super().write(vals)
        if not self.env["connect.settings"].get_param("voicetel_auto_sync"):
            return res
        if self.env.context.get('skip_voicetel_sync'):
            return res
        if not (set(['name', 'code_type', 'model', 'method']) & set(vals.keys())):
            return res
        client = self.env['connect.settings'].get_voicetel_client()
        for rec in self:
            rec.update_voicetel_app(client)
        return res

    @api.private
    def update_voicetel_app(self, client):
        """Push this record to its Application; create it when it is gone.

        The application is looked up by SID only -- never re-created on an
        unrelated failure -- so a sync cannot leave orphan Applications
        behind.
        """
        self.ensure_one()
        if not self.sid:
            return self.create_voicetel_app(client)
        try:
            remote = client.applications.get(self.sid)
        except NotFoundError:
            debug(self, 'VoiceML app {} (SID {}) not found, creating.'.format(
                self.name, self.sid))
            return self.create_voicetel_app(client)
        # A database restored from another one (staging from production)
        # holds the same SIDs: never repoint an application that serves
        # another Odoo.
        remote_host = (urlparse(getattr(remote, 'voice_url', None) or '').hostname or '').lower()
        our_host = (urlparse(self.voice_url or '').hostname or '').lower()
        if remote_host and remote_host != our_host:
            logger.warning(
                'VoiceML application %s points at %s, not %s; left alone',
                self.sid, remote_host, our_host)
            return remote
        application = client.applications.update(
            self.sid, UpdateApplicationRequest(
                VoiceUrl=self.voice_url,
                VoiceMethod='POST',
                VoiceFallbackUrl=self.voice_fallback_url,
                VoiceFallbackMethod='POST',
                FriendlyName=self.name,
                StatusCallback=self.voice_status_url,
                StatusCallbackMethod='POST',
            ))
        debug(self, 'VoiceML app {} updated.'.format(self.name))
        return application

    def unlink(self):
        if not self.env["connect.settings"].get_param("voicetel_auto_sync"):
            return super().unlink()
        client = self.env['connect.settings'].get_voicetel_client()
        for rec in self:
            if rec.sid:
                try:
                    client.applications.delete(rec.sid)
                except NotFoundError:
                    logger.warning('Cannot delete app %s in VoiceML, not found', rec.name)
        return super().unlink()

    @api.model
    def sync(self):
        self.env['connect.settings']._voicetel_check_admin()
        client = self.env['connect.settings'].get_voicetel_client()
        for rec in self.search([]):
            rec.update_voicetel_app(client)

    def _get_voicetel_urls(self):
        settings = self.env['connect.settings'].sudo()
        fallback_url = settings.get_param('api_fallback_url')
        for rec in self:
            rec.voice_status_url = settings.voicetel_webhook_url('voicetel/webhook/callstatus')
            rec.voice_url = settings.voicetel_webhook_url(
                'voicetel/webhook/voiceml/{}'.format(rec.id))
            if fallback_url:
                rec.voice_fallback_url = '{}/voicetel/webhook/voiceml/{}'.format(
                    fallback_url.rstrip('/'), rec.id)
            else:
                rec.voice_fallback_url = ''

    @api.private
    def render(self, request=None, params=None):
        # Copy into fresh dicts so that nothing mutates the caller's state.
        request = dict(request or {})
        params = dict(params or {})
        caller_env = self.env
        self = self.sudo()
        api_url_check = self.env['connect.settings'].check_api_url()
        if api_url_check:
            response = VoiceResponse()
            response.say(api_url_check)
            return response.to_xml()
        self.ensure_one()
        settings = self.env['connect.settings'].sudo()
        params.update({
            'recording_voice_status_url': settings.voicetel_webhook_url(
                'voicetel/webhook/recordingstatus'),
            'call_voice_status_url': settings.voicetel_webhook_url(
                'voicetel/webhook/callstatus'),
        })
        if self.code_type == 'voiceml':
            res = self.render_voiceml(request=request, params=params)
        elif self.code_type == 'model_method':
            if (self.model, self.method) not in ALLOWED_APP_METHODS:
                logger.error(
                    'VoiceML app %s refused: %s.%s is not an allowed entry point',
                    self.id, self.model, self.method)
                return REJECT
            # Dispatch as the webhook caller, not as superuser.
            res = str(getattr(caller_env[self.model], self.method)(
                request=request, params=params))
        else:
            return REJECT
        debug(self, 'VoiceML render result: %s' % pretty_xml(res))
        return res

    @api.private
    def render_voiceml(self, request=None, params=None):
        ctx = dict(request or {})
        ctx.update(params or {})
        return render_template(self.voiceml, ctx)

    @api.constrains('code_type', 'model', 'method')
    def _check_model_method(self):
        for rec in self:
            if rec.code_type == 'model_method' and (
                    rec.model, rec.method) not in ALLOWED_APP_METHODS:
                raise ValidationError(
                    'model.method applications may only call: {}'.format(
                        ', '.join('{}.{}'.format(*k) for k in sorted(ALLOWED_APP_METHODS))))

    def create_extension(self):
        self.ensure_one()
        return self.env['connect.voicetel.exten'].create_extension(self, self._name)
