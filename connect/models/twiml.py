# -*- coding: utf-8 -*-

import datetime
import jinja2
import logging
import random
import re
import time
from datetime import timedelta
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

# Rate-limit window for fallback chatter posts. Rendering a TwiML body
# happens once per inbound call; an archived-audio reference could otherwise
# flood the audio / twiml chatter with a post per call. 15 min is loose
# enough to keep the signal visible but cheap in busy flows.
#
# Rate-limit state is tracked PER-TARGET, not per (audio, twiml) pair:
#   - `connect.audio.last_fallback_logged_on` is per-archived-audio.
#     If twiml A posts at t=0, twiml B referencing the SAME archived
#     audio at t=5min has its chatter SUPPRESSED. Twiml-level visibility
#     of archived references is via the search filter "References
#     Archived Audio"; the audio-side chatter is operator awareness that
#     the archived audio is still being cited somewhere.
#   - `connect.twiml.last_unresolved_logged_on` is per-twiml. Unresolved
#     UUIDs aren't tied to any audio record, so per-twiml is the natural
#     granularity: one rate-limited notice per broken body.
_FALLBACK_CHATTER_WINDOW = timedelta(minutes=15)

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
    # mail.thread added alongside the referrer mixin so the audio() helper
    # can post fallback events to this record's chatter (unresolved UUID
    # path). No tracking= on fields — we only use the chatter message stream.
    _inherit = ['connect.audio.referrer.mixin', 'mail.thread']
    _description = 'TwiML app'
    _order = 'name'

    # M2m shape — the body can reference any number of audios. The mixin
    # default walks m2o fields; _collect_referenced_audio_ids is overridden
    # below to pull from this M2m instead.
    _audio_reference_fields = ('referenced_audio_ids',)
    # Body + code_type edits can change which audios are referenced without
    # appearing directly as an _audio_reference_fields write (the M2m is a
    # stored compute, not a user-written field). Trigger ref-refresh on any
    # of them so Where-Used and the state machine keep up. `exten=None`
    # means the TwiML is orphaned (not wired to any extension) — the
    # Where-Used row should flip inactive.
    _audio_reference_trigger_fields = ('twiml', 'twipy', 'code_type', 'exten')
    # Reachability: any change to the body, type, or exten wiring flips
    # which audios the twiml could play on a live call.
    _audio_reachability_fields = ('twiml', 'twipy', 'code_type', 'exten')

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
    referenced_audio_ids = fields.Many2many(
        'connect.audio',
        compute='_compute_referenced_audio_ids',
        store=True,
        string='Referenced Audio',
        help='Audios invoked by this TwiML via audio("<uuid>") tokens. '
             'Populated by scanning the template source on save; keeps the '
             'routing graph and Where-Used reflecting reality. For '
             'code_type=model_method the set is always empty — model.method '
             'dispatch is ungoverned by the audio library.',
    )
    last_unresolved_logged_on = fields.Datetime(
        readonly=True,
        help='When the audio() helper most recently posted an unresolved-'
             'uuid notice to this twiml\'s chatter. Rate-limited per-twiml '
             '(the unresolved UUID isn\'t tied to any audio record, so the '
             'twiml is the natural granularity). Suppresses repeat posts '
             'within the fallback chatter window so high-traffic flows '
             'don\'t flood the log.',
    )

    @api.depends('twiml', 'twipy', 'code_type')
    def _compute_referenced_audio_ids(self):
        Audio = self.env['connect.audio'].sudo()
        for rec in self:
            if rec.code_type == 'twiml':
                body = rec.twiml or ''
            elif rec.code_type == 'twipy':
                body = rec.twipy or ''
            else:
                # model_method — ungoverned by the audio library. Whatever
                # the dispatched Python does with connect.audio is its own
                # business; we don't try to static-analyse arbitrary method
                # bodies.
                rec.referenced_audio_ids = [(5, 0, 0)]
                continue
            # Lowercase at the scanner boundary — connect.audio.uuid is
            # stored lowercase (uuid4 default + DB unique index is case-
            # sensitive). Bodies may use uppercase hex from copy-paste.
            uuids = {u.lower() for u in _AUDIO_CALL_RE.findall(body)}
            if not uuids:
                rec.referenced_audio_ids = [(5, 0, 0)]
                continue
            # active_test=False — archived audios (active=False) must be
            # included so the 'References Archived Audio' search filter
            # (domain on referenced_audio_ids.state) can surface twimls
            # citing retired audios. Mirrors the render-time helper.
            audios = Audio.with_context(active_test=False).search(
                [('uuid', 'in', list(uuids))])
            # Warn on misses so operators see dropped references in logs —
            # the helper will also log at render time, but catching them at
            # save time gives a faster feedback loop.
            hits = set(audios.mapped('uuid'))
            missing = uuids - hits
            if missing:
                logger.warning(
                    'connect.twiml#%s body references unknown audio uuids: %s',
                    rec.id, sorted(missing))
            rec.referenced_audio_ids = [(6, 0, audios.ids)]

    def _collect_referenced_audio_ids(self):
        """Override mixin default — our reference field is an M2m, not an m2o.

        The mixin's base implementation treats each entry in
        _audio_reference_fields as a Many2one. TwiML bodies can cite any
        number of audios, so the M2m is the natural shape — but we have
        to surface its ids in the set the mixin uses to drive reference
        refresh and reachability recomputes.
        """
        ids = set()
        for rec in self:
            ids.update(rec.referenced_audio_ids.ids)
        return ids


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
        # Selection is twiml/twipy/model_method — the legacy 'python' value
        # never existed in this schema, so this guard was dead code that
        # silently let broken TwiPy bodies save. Match the actual value.
        if self.code_type == 'twipy' and self.twipy:
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

        Dynamic audios (is_dynamic=True) substitute `{field.path}` tokens
        from a record. The helper accepts a `record` kwarg for this. Jinja
        callers pass it explicitly: `{{ audio('<uuid>', record=some_rec) }}`.
        TwiPy authors likewise: `audio('<uuid>', record=rec)`. When omitted,
        we fall back to `request.get('record')` then `params.get('record')`
        so webhook handlers that already thread a record through these
        dicts get it for free. A dynamic audio with no record resolves
        to empty Markup + warning (same as an unknown UUID).

        Fallback routing (Phase 2):
          - unresolved UUID → plays `fallback.unresolved`, logs WARNING
            and posts to THIS twiml's chatter (rate-limited per twiml).
          - live-found-but-archived audio → plays `fallback.archived`,
            logs WARNING and posts to the archived AUDIO's chatter so
            operators see the notice on the audio record (rate-limited
            per audio).
          - live/draft/reviewed audio → normal play_on, no log, no post.
        """
        self.ensure_one()
        Audio = self.env['connect.audio'].sudo()

        # Surface an ambient record from request/params so Jinja authors
        # don't have to plumb it through every helper call. Explicit kwarg
        # on the helper still wins.
        ambient_record = None
        if request and isinstance(request, dict):
            ambient_record = request.get('record')
        if ambient_record is None and params and isinstance(params, dict):
            ambient_record = params.get('record')

        def _audio(uuid_str, record=None):
            normalized = (uuid_str or '').lower()
            # active_test=False — archived rows have active=False and would
            # otherwise be invisible to the default search; we must see
            # them to route to the archived-fallback path rather than
            # falling through to the unresolved path.
            audio = Audio.with_context(active_test=False).search(
                [('uuid', '=', normalized)], limit=1)
            if not audio:
                return self._render_fallback(
                    reason='unresolved',
                    source_uuid=uuid_str,
                    ambient_record=ambient_record)
            # Archived audio (state OR active=False — Odoo's built-in
            # archive also short-circuits the original audio's render
            # path) routes to the archived-fallback. Keep draft/reviewed
            # on the normal path: they're pre-release states, not retired.
            if audio.state == 'archived' or not audio.active:
                return self._render_fallback(
                    reason='archived',
                    source_uuid=uuid_str,
                    archived_audio=audio,
                    ambient_record=ambient_record)
            return self._render_audio_inner(
                audio, record=record or ambient_record, uuid_str=uuid_str)

        return _audio

    def _render_audio_inner(self, audio, record=None, uuid_str=None):
        """Run play_on() against a scratch VoiceResponse, return inner verbs.

        Shared by the live and fallback paths so both go through identical
        XML serialization. Narrow ValidationError/ValueError swallow (bad
        template, missing record) → empty Markup with logger.exception.
        """
        self.ensure_one()
        from twilio.twiml.voice_response import VoiceResponse
        response = VoiceResponse()
        try:
            audio.play_on(response, record=record)
        except (ValidationError, ValueError) as e:
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

    def _render_fallback(self, reason, source_uuid=None,
                         archived_audio=None, ambient_record=None):
        """Render the configured fallback audio for `reason` + log.

        reason ∈ {'unresolved', 'archived'} picks the system_key to look
        up (fallback.unresolved / fallback.archived) and which record gets
        a chatter post. If the fallback audio itself is missing (a
        misconfigured install), returns a comment-style empty Markup and
        logs ERROR rather than crashing the render. Chatter posts are
        rate-limited per-target (per-archived-audio for the archived
        path, per-twiml for the unresolved path — see
        _FALLBACK_CHATTER_WINDOW) so high-traffic flows don't flood the
        stream.
        """
        self.ensure_one()
        Audio = self.env['connect.audio'].sudo()
        system_key = (
            'fallback.archived' if reason == 'archived'
            else 'fallback.unresolved')
        # Grep anchor: 'connect.twiml audio_fallback' — scan ops logs
        # for this to audit dropped references / archived-ref leakage.
        logger.warning(
            'connect.twiml audio_fallback reason=%s twiml=%s uuid=%s',
            reason, self.id or 'unsaved', source_uuid)
        fallback_audio = Audio.search(
            [('system_key', '=', system_key)], limit=1)
        if not fallback_audio:
            # Corrupted data — the fallback itself is missing. We cannot
            # synthesise a <Say> here (no voice resolution, no SSML
            # processing), so return an inert comment marker and log
            # ERROR. A valid install ships both fallbacks via data/audio.xml.
            logger.error(
                'connect.twiml#%s fallback audio %r missing; no inner '
                'verbs will be emitted for reason=%s uuid=%s',
                self.id, system_key, reason, source_uuid)
            return Markup('<!-- connect: fallback audio missing -->')
        # Post to chatter on the right target. Unresolved posts on the
        # twiml (operators look there for "why is this body broken"),
        # archived posts on the audio (operators look there for "who's
        # still calling me").
        self._post_fallback_chatter(
            reason=reason, source_uuid=source_uuid,
            archived_audio=archived_audio)
        return self._render_audio_inner(
            fallback_audio, record=ambient_record, uuid_str=source_uuid)

    def _post_fallback_chatter(self, reason, source_uuid=None,
                               archived_audio=None):
        """Rate-limited chatter post on the twiml (unresolved) or the
        archived audio (archived). Silently no-ops when inside the
        cooldown window; any error is logged, never raised.
        """
        self.ensure_one()
        now = fields.Datetime.now()
        cutoff = now - _FALLBACK_CHATTER_WINDOW
        try:
            if reason == 'unresolved':
                # Skip if we posted within the window. A save on the same
                # transaction path could hit a pre-save record (self.id
                # falsy) — no chatter for unsaved drafts; those only exist
                # in tests that render before committing.
                if not self.id:
                    return
                if (self.last_unresolved_logged_on
                        and self.last_unresolved_logged_on > cutoff):
                    return
                body = Markup(
                    '<p>TwiML audio() helper rendered the '
                    '<code>fallback.unresolved</code> audio because '
                    'UUID <code>%s</code> does not match any '
                    'connect.audio record.</p>'
                ) % (source_uuid or '')
                self.sudo().message_post(
                    body=body,
                    message_type='notification',
                    subtype_xmlid='mail.mt_note')
                # Write after post so a failure in message_post doesn't
                # silently suppress the next attempt's chatter entry.
                self.sudo().write(
                    {'last_unresolved_logged_on': now})
            elif reason == 'archived' and archived_audio:
                # No chatter for unsaved drafts — the link we'd render
                # would have data-oe-id="0", a dead anchor. Mirrors the
                # unresolved guard above.
                if not self.id:
                    return
                if (archived_audio.last_fallback_logged_on
                        and archived_audio.last_fallback_logged_on > cutoff):
                    return
                # Link the twiml record in the message so operators can
                # jump straight to the referrer.
                twiml_link = Markup(
                    '<a href="#" data-oe-model="connect.twiml" '
                    'data-oe-id="%d">%s</a>'
                ) % (self.id,
                     (self.display_name or f'connect.twiml#{self.id}'))
                body = Markup(
                    '<p>TwiML audio() helper rendered the '
                    '<code>fallback.archived</code> audio in place of '
                    'this archived audio. Referenced from: %s.</p>'
                ) % twiml_link
                archived_audio.sudo().message_post(
                    body=body,
                    message_type='notification',
                    subtype_xmlid='mail.mt_note')
                archived_audio.sudo().write(
                    {'last_fallback_logged_on': now})
        except Exception as e:
            # Chatter bookkeeping is never critical enough to break a
            # render. Log and move on.
            logger.warning(
                'connect.twiml#%s fallback chatter post failed '
                '(reason=%s): %s', self.id, reason, e)

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
# audio: audio('<uuid>', record=None) - renders a connect.audio by UUID
#        and returns the inner TwiML verbs as a string. Embed in template
#        text or splice into a response with response.append() / str
#        concat. Pass `record=` for is_dynamic=True audios so {field.path}
#        substitutions resolve; when omitted, request['record'] or
#        params['record'] is used if present. The UUID is visible on the
#        audio form via the Copy UUID button.

response = VoiceResponse()
user_name = self.env.user.name
response.say('Welcome {} to the world of Connect!'.format(user_name))
self.twiml = response
"""
