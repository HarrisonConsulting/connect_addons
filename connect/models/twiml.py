# -*- coding: utf-8 -*-

import datetime
import jinja2
import logging
import random
import re
import time
from urllib.parse import urljoin
from xml.dom.minidom import parseString
from xml.etree import ElementTree as ET

from markupsafe import Markup

from odoo import fields, models, api, release
from odoo.exceptions import ValidationError
from .settings import debug


# UUID4 reference pattern: audio('<uuid>') / audio("<uuid>") with optional
# whitespace. Scanned on save to populate referenced_audio_ids so the audio
# library stays in sync with TwiML/TwiPy bodies.
_AUDIO_CALL_RE = re.compile(
    r'audio\(\s*[\'"]([0-9a-fA-F-]{36})[\'"]\s*\)')

logger = logging.getLogger(__name__)

# Make XML pretty.
def pretty_xml(content):
    try:
        dom = parseString(str(content))
        pretty_content = dom.toprettyxml()
        # Filter out empty lines
        return "\n".join([line for line in pretty_content.splitlines() if line.strip()])
    except Exception as e:
        logger.error('Pretty XML parse error: %s', e)
        return 'Pretty XML parse error: {}\n{}'.format(e, str(content))


class TwiML(models.Model):
    _name = 'connect.twiml'
    _description = 'TwiML app'
    _order = 'name'

    sid = fields.Char('SID', readonly=True)
    old_sid = fields.Char('Old SID', readonly=True, help="Previous SID value, used during region migration to reuse existing apps")
    name = fields.Char(required=True)
    description = fields.Text()
    code_type = fields.Selection([
        ('twiml', 'TwiML'),
        ('twipy', 'TwiPy'),
        ('model_method', 'model.method')
        ], help='Type of the language. To call mymodel.render() set mymodel.render as model.method',
        required=True, default='twiml')
    twiml = fields.Text(required=True, string='TwiML',
        default=pretty_xml('<?xml version="1.0" encoding="UTF-8"?><Response><Say>Hello</Say></Response>'))
    twipy = fields.Text('TwiPy')
    model = fields.Char()
    method = fields.Char()
    voice_url = fields.Char(compute='_get_twilio_urls', compute_sudo=True)
    voice_fallback_url = fields.Char(compute='_get_twilio_urls', compute_sudo=True)
    voice_status_url = fields.Char(compute='_get_twilio_urls', compute_sudo=True)
    exten = fields.Many2one('connect.exten', ondelete='set null', readonly=True)
    exten_number = fields.Char(related='exten.number')


    def _find_twilio_app_by_old_sid(self, client):
        """Find existing Twilio app by old_sid for region migration.

        Returns:
            Twilio Application object if found by old_sid, None otherwise
        """
        self.ensure_one()
        if not self.old_sid:
            return None

        try:
            # Try to get the app by old_sid
            application = client.applications(self.old_sid).fetch()
            debug(self, 'Found existing TwiML app {} by old_sid {} during region migration.'.format(
                application.friendly_name, self.old_sid))
            return application
        except Exception as e:
            if 'not found' in str(e):
                debug(self, 'No existing TwiML app found by old_sid {} during region migration.'.format(self.old_sid))
                return None
            else:
                # Re-raise unexpected errors
                raise

    def create_twilio_app(self, client):
        self.ensure_one()
        # Store current sid as old_sid before creating new app
        old_sid_to_store = self.sid

        application = client.applications.create(
            voice_url=self.voice_url,
            voice_fallback_url=self.voice_fallback_url,
            friendly_name=self.name,
            status_callback=self.voice_status_url,
        )

        # Update sids: new sid from Twilio, old_sid from previous value
        self.write({
            'sid': application.sid,
            'old_sid': old_sid_to_store
        })

        debug(self, 'Created TwiML app {} in Twilio.'.format(self.name))
        return application

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get('install_mode') == True:
            # We do not create TwiML on installation as we don't have API keys yet.
            return super().create(vals_list)
        client = self.env['connect.settings'].get_client()
        records = super().create(vals_list)
        if client:
            for rec in records:
                rec.create_twilio_app(client)
        return records

    def write(self, vals):
        res = super().write(vals)
        # Check if twilio_auto_sync is disabled
        if not self.env["connect.settings"].get_param("twilio_auto_sync"):
            return res

        client = self.env['connect.settings'].get_client()
        for rec in self:
            if rec.sid:
                rec.update_twilio_app(client)
        return res

    def update_twilio_app(self, client):
        self.ensure_one()

        # First, try to update the app using current sid
        if self.sid:
            try:
                application = client.applications(self.sid).update(
                    voice_url=self.voice_url,
                    voice_fallback_url=self.voice_fallback_url,
                    friendly_name=self.name,
                    status_callback=self.voice_status_url,
                )
                debug(self, 'TwiML app {} updated.'.format(self.name))
                return application
            except Exception as e:
                if 'not found' in str(e):
                    debug(self, 'TwiML app {} not found by current sid {}, checking for region migration.'.format(
                        self.name, self.sid))
                    # App not found by current sid, try to find by old_sid for region migration
                    existing_app = self._find_twilio_app_by_old_sid(client)
                    if existing_app:
                        # Found existing app by old_sid, update it and swap sids
                        debug(self, 'Reusing existing TwiML app {} during region migration.'.format(
                            existing_app.friendly_name))

                        # Update the existing app with current configuration
                        application = client.applications(existing_app.sid).update(
                            voice_url=self.voice_url,
                            voice_fallback_url=self.voice_fallback_url,
                            friendly_name=self.name,
                            status_callback=self.voice_status_url,
                        )

                        # Swap sids: current sid becomes old_sid, existing app sid becomes current sid
                        old_current_sid = self.sid
                        self.write({
                            'sid': existing_app.sid,
                            'old_sid': old_current_sid
                        })

                        debug(self, 'TwiML app {} updated during region migration. SID: {} -> {}'.format(
                            self.name, old_current_sid, existing_app.sid))
                        return application
                    else:
                        # No existing app found by old_sid, create a new one
                        debug(self, 'No existing TwiML app found for region migration, creating new app.')
                        return self.create_twilio_app(client)
                else:
                    # Re-raise unexpected errors
                    raise
        else:
            # No current sid, create new app
            debug(self, 'No current SID for TwiML app {}, creating new app.'.format(self.name))
            return self.create_twilio_app(client)

    def unlink(self):
        # Check if twilio_auto_sync is disabled
        if not self.env["connect.settings"].get_param("twilio_auto_sync"):
            return super().unlink()

        client = self.env['connect.settings'].get_client()
        for rec in self:
            if rec.sid:
                try:
                    client.applications(rec.sid).delete()
                except Exception as e:
                    if 'not found' in str(e):
                        logger.warning('Cannot delete app %s in Twilio, not found', rec.name)
                    else:
                        raise
        return super().unlink()

    @api.model
    def sync(self):
        client = self.env['connect.settings'].get_client()
        for rec in self.search([]):
            rec.update_twilio_app(client)

    def _get_twilio_urls(self):
        api_url = self.env['connect.settings'].get_param('api_url')
        fallback_url = self.env['connect.settings'].get_param('api_fallback_url')
        edge = self.env['connect.settings'].get_param('twilio_edge')
        for rec in self:
            rec.voice_status_url = urljoin(api_url, 'twilio/webhook/callstatus#e={}'.format(edge))
            rec.voice_url = urljoin(api_url, 'twilio/webhook/twiml/{}#e={}'.format(rec.id, edge))
            if fallback_url:
                rec.voice_fallback_url = urljoin(fallback_url, 'twilio/webhook/twiml#e={}'.format(edge))
            else:
                rec.voice_fallback_url = ''

    @api.constrains('twipy')
    def _check_syntax(self):
        if self.code_type == 'python' and self.twipy:
            self.render()

    def render(self, request={}, params={}):
        # Render under admin privs. Also do not check Twilio Signature here!
        self = self.sudo()
        api_url_check = self.env['connect.settings'].check_api_url()
        if api_url_check:
            return '<Response><Say>{}</Say></Response>'.format(api_url_check)
        self.ensure_one()
        api_url = self.env['connect.settings'].sudo().get_param('api_url')
        edge = self.env['connect.settings'].sudo().get_param('twilio_edge')
        recording_voice_status_url = urljoin(api_url, 'app/connect/webhook/recordingstatus#e={}'.format(edge))
        call_voice_status_url = urljoin(api_url, 'app/connect/webhook/callstatus#e={}'.format(edge))
        params.update({
            'recording_voice_status_url': recording_voice_status_url,
            'call_voice_status_url': call_voice_status_url,
        })
        # Return twiml as is if it's not python
        if self.code_type == 'twiml':
            res = self.render_twiml(request=request, params=params)
        elif self.code_type == 'twipy':
            res = self.render_python(request=request, params=params)
        elif self.code_type == 'model_method':
            res = str(getattr(self.env[self.model], self.method)(request=request, params=params))
        debug(self, 'TwiML render result: %s' % pretty_xml(res))
        return res

    def render_twiml(self, request={}, params={}):
        environment = jinja2.Environment()
        environment.globals['audio'] = self._make_audio_helper(
            request=request, params=params)
        template = environment.from_string(self.twiml)
        # Join request and params value and render the final TwiML.
        request.update(params)
        res = template.render(**request)
        return res

    def render_python(self, request={}, params={}):
        import twilio
        # Render
        try:
            exec(self.twipy, {}, {
                'logger': logger,
                'request': request,
                'params': params,
                'twilio': twilio,
                'user': self.env.user,
                'context': self.env.context,
                'env': self.env,
                'rec': self,
                'self': self,
                'pretty_xml': pretty_xml,
                'random': random,
                'datetime': datetime,
                'time': time,
                # audio('<uuid>') returns a Markup string of the inner TwiML
                # verbs produced by connect.audio.play_on(). Authors splice it
                # into a response.append() call or embed it in template text.
                'audio': self._make_audio_helper(
                    request=request, params=params),
            })
            # We expect that twipy final line is to assign the result to twiml field.
            return self.twiml
        except Exception as e:
            logger.exception('TwiML render error:')
            raise ValidationError(str(e))

    def _make_audio_helper(self, request=None, params=None):
        """Return a closure that resolves `audio('<uuid>')` → inner TwiML.

        Used as a Jinja global in render_twiml and injected into the TwiPy
        exec globals in render_python. The closure:

          1. Looks up the connect.audio by its UUID (sudo — TwiML authors
             don't need audio ACLs to produce a <Say>/<Play>).
          2. Runs play_on() against a scratch VoiceResponse so all existing
             audio rendering logic (utterance cache, pronunciation, voice
             resolution) applies uniformly.
          3. Strips the outer <Response> wrapper and returns a Markup of
             the inner verbs so Jinja doesn't HTML-escape angle brackets
             when substituting into the body.

        Unknown UUIDs render as empty Markup and log a warning — Phase 1
        behavior. TODO(phase-2): swap the silent miss for a configurable
        fallback audio (per-referrer override + module default) so a typo
        or a deleted audio never produces a silent leg; Phase 2 will also
        add the observability counter (missed/fallback/served).
        """
        self.ensure_one()
        Audio = self.env['connect.audio'].sudo()

        # Import lazily — the twilio lib is an external dep and we don't
        # want this module import to fail in environments where the TwiML
        # bodies never get rendered (test harnesses, static analysis).
        from twilio.twiml.voice_response import VoiceResponse

        def _audio(uuid_str):
            audio = Audio.search([('uuid', '=', uuid_str)], limit=1)
            if not audio:
                # TODO(phase-2): fall back to a configured placeholder audio
                # and count the miss. For now: silent + warning so an
                # operator sees dropped audio references in logs.
                logger.warning(
                    'connect.twiml#%s referenced unknown audio uuid %r',
                    self.id, uuid_str)
                return Markup('')
            response = VoiceResponse()
            try:
                audio.play_on(response)
            except Exception as e:
                logger.exception(
                    'connect.twiml#%s audio(%r) play_on failed: %s',
                    self.id, uuid_str, e)
                return Markup('')
            # VoiceResponse.__str__ emits a full XML document with the
            # <Response> wrapper. We want only the inner verbs so the
            # caller can splice them into an existing <Response>.
            try:
                root = ET.fromstring(str(response))
                inner = ''.join(
                    ET.tostring(child, encoding='unicode')
                    for child in root)
            except ET.ParseError as e:
                logger.exception(
                    'connect.twiml#%s audio(%r) XML parse failed: %s',
                    self.id, uuid_str, e)
                return Markup('')
            return Markup(inner)

        return _audio

    def create_extension(self):
        self.ensure_one()
        return self.env['connect.exten'].create_extension(self, 'twiml')

    @api.onchange('code_type')
    def _set_default_twipy_code(self):
        if self.code_type == 'twipy' and not self.twipy:
            self.twipy = """from twilio.twiml.voice_response import VoiceResponse, Dial, Gather, Say, Hangup

# datetime: Python datetime library.
# logger: logger - logger.info('test')
# random: Python random library.
# request: request - dict, call data from the Twilio request.
# params: params - dict, additional params set by the request handler.
# time: Python time library.
# twilio: twilio - Twilio python library.
# self: curreny TwiPy recordset.
# audio: audio('<uuid>') - renders a connect.audio by UUID and returns the
#        inner TwiML verbs as a string. Embed in template text or splice
#        into a response with response.append() / str concat. The UUID is
#        visible on the audio form via the Copy UUID button.

response = VoiceResponse()
user_name = self.env.user.name
response.say('Welcome {} to the world of Connect!'.format(user_name))
self.twiml = response
"""
