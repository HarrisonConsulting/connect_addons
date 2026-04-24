# -*- coding: utf-8 -*-

import base64
import logging
import re
import uuid as uuid_lib
from urllib.parse import urlsplit, quote
from markupsafe import escape
from psycopg2 import IntegrityError
from odoo import fields, models, api
from odoo.exceptions import ValidationError
from odoo.models import Constraint

from . import audio_ulaw
from .tts_mixin import DEFAULT_TWILIO_VOICE

logger = logging.getLogger(__name__)


SOURCES = [
    ('record', 'Browser Recording'),
    ('twilio_tts', 'Twilio TTS'),
    ('external_url', 'External URL'),
    ('attachment', 'Internal Attachment'),
]


STATES = [
    ('draft', 'Draft'),
    ('reviewed', 'Reviewed'),
    ('live', 'Live'),
    ('archived', 'Archived'),
]

# Mimetypes Twilio's <Play> verb accepts. WebM/Opus is NOT in the list.
# audio/x-wav and audio/wave remain accepted aliases on INPUT (some producers
# still emit them; the libmagic auto-detection on ir.attachment can flip
# between audio/wav and audio/x-wav depending on deployment). We canonicalize
# to audio/wav on OUTPUT. audio/basic is omitted: our transcoder emits
# RIFF-wrapped μ-law (format 7 within a WAVE container), which is audio/wav,
# never headerless.
TWILIO_PLAYABLE_MIMETYPES = {
    'audio/mpeg', 'audio/mp3',
    'audio/wav', 'audio/x-wav', 'audio/wave',
    'audio/aiff', 'audio/x-aiff',
    'audio/gsm', 'audio/x-gsm',
}

# Default server-side ceiling on recording bytes post-transcode. Overridable
# via ir.config_parameter 'connect.audio.max_recording_bytes'. 5 MB ~= 10 min
# of 8 kHz μ-law mono, comfortable headroom for IVR prompts. The JS widget
# carries a matching static cap as a client-side courtesy; the server is the
# trust boundary.
DEFAULT_MAX_RECORDING_BYTES = 5 * 1024 * 1024

TOKEN_RE = re.compile(r'\{([a-zA-Z_][a-zA-Z0-9_.]*)\}')

# Helpers for migrating legacy Jinja templates ({{user.name}}) to the new
# {token} syntax. Anything outside the {{<root>.<dotted.path>}} pattern
# (filters, control flow, other variables) is unconvertible.
_JINJA_VAR_RE = re.compile(r'\{\{(.+?)\}\}', re.DOTALL)
_JINJA_TOKEN_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_.]*$')


class Audio(models.Model):
    """Addressable audio asset.

    Pointed at by voicemails, greetings, callflow prompts. Source determines
    where the audio comes from; is_dynamic enables {field.path} substitution
    against a record passed to render().

    Rendering is dispatched via _audio_renderers (a dict on the class). Modules
    extend by inheriting and adding entries in setup() or via _inherit.
    """
    _name = 'connect.audio'
    _inherit = ['mail.thread']
    _description = 'Audio'
    _order = 'name'

    name = fields.Char(required=True,
        help='Display name for this audio asset.')
    uuid = fields.Char(
        string='UUID',
        default=lambda self: str(uuid_lib.uuid4()),
        copy=False,
        index=True,
        required=True,
        readonly=True,
        help='Stable opaque identifier referenced from TwiML/TwiPy bodies via '
             'audio("<uuid>") to keep the audio library and the routing graph '
             'synchronized. Settable in XML data files for seeded audios so '
             'references survive fresh installs. Immutable after creation — '
             'changing it would silently break every reference.',
    )
    description = fields.Char(
        help='Optional free-text description of what this audio says or where '
             'it should be used. Not surfaced to callers.')
    system_key = fields.Char(index=True,
        help='Key used by tts_mixin.tts_system_message() to look up this audio.')
    source = fields.Selection(SOURCES, required=True, default='twilio_tts')
    is_dynamic = fields.Boolean(default=False,
        help='When set, static_text is treated as a template with {field.path} tokens '
             'resolved at render() time against a passed record. Only valid for TTS sources.')
    use_default_voice = fields.Boolean(default=True,
        help='When True, this audio speaks in the per-source database default '
             '(settings.default_twilio_voice for Twilio; provider extensions '
             'register their own defaults) and any value in voice_id is ignored. '
             'Uncheck to pin a specific voice on this audio.')
    voice_id = fields.Many2one('connect.voice', ondelete='set null',
        help='Voice for TTS sources when use_default_voice is False. Ignored '
             'for record/external_url/attachment and whenever use_default_voice '
             'is True.')
    resolved_voice_id = fields.Many2one('connect.voice',
        compute='_compute_resolved_voice', string='Resolved Voice',
        help='Voice that will actually be used to render this audio — '
             'voice_id when use_default_voice is False, else the per-source '
             'database default. Read-only view helper.')
    static_text = fields.Text(
        help='Text to speak (TTS sources) or template body (when is_dynamic).')
    static_url = fields.Char(help='External audio URL (source=external_url).')
    attachment_id = fields.Many2one('ir.attachment',
        domain=[('mimetype', 'like', 'audio/%')],
        help='Internal ir.attachment with audio mimetype (source=attachment).')
    recording_file = fields.Binary(attachment=True,
        help='Browser-recorded audio bytes (source=record). Transcoded to '
             '8 kHz mono μ-law WAV on save so Twilio can stream it to the '
             'PSTN without live resampling.')
    recording_filename = fields.Char(
        help='Browser-supplied filename for the recorded audio. Used as the '
             'Content-Disposition filename when serving to Twilio.')
    recording_mimetype = fields.Char(default='audio/wav',
        help='MIME type of the transcoded recording. Always an entry from '
             'TWILIO_PLAYABLE_MIMETYPES after the transcoder has run.')
    model_id = fields.Many2one('ir.model',
        help='Model whose fields can be referenced in {token} substitutions when is_dynamic.')
    model_name = fields.Char(related='model_id.model', string='Model Name',
        store=True, readonly=True,
        help='Stored mirror of model_id.model for efficient domain filtering.')
    utterance_ids = fields.One2many('connect.audio.utterance', 'audio_id',
        help='All cached utterances rendered from this audio — one per '
             '(voice, rendered text, provider params) tuple.')
    utterance_count = fields.Integer(compute='_compute_utterance_count',
        help='Count of cached utterances; a proxy for render activity.')

    state = fields.Selection(STATES, required=True, default='draft', index=True,
        tracking=True,
        help="Lifecycle state. draft → reviewed (manual sign-off) → live "
             "(auto when an active referrer exists) → archived (manual "
             "retirement). Archived stays archived until explicitly unarchived.")
    reference_summary = fields.Char(compute='_compute_reference_summary',
        store=True,
        help='One-line digest of where this audio is used — e.g. '
             '"2 callflows (1 live) · 1 user".')
    last_generated_on = fields.Datetime(compute='_compute_last_generated_on',
        store=True, string='Last Generated',
        help='Most recent utterance generation timestamp. Computed directly '
             'from utterance_ids rather than related through latest_utterance_id '
             'so Odoo can invert the dependency when an utterance is (re)created.')
    last_played_on = fields.Datetime(compute='_compute_last_played_on',
        store=True, string='Last Played',
        help='Most recent time any utterance of this audio was served to '
             'Twilio via the signed URL. A reliable lower bound on '
             'real-world playback — supports "stale prompt" detection for '
             'archive decisions.')
    is_reachable = fields.Boolean(default=False, index=True,
        help='True when a may-reach BFS from live entry points (inbound DIDs, '
             'running callouts) can touch this audio. Recomputed asynchronously '
             'whenever a routing edge changes; system_key audios are always '
             'treated as reachable because code dispatches them at runtime.')
    active = fields.Boolean(default=True, tracking=True,
        help='Archived audio has active=False and is hidden from default views. '
             'Use Action → Archive / Unarchive. System audio cannot be archived.')
    archived_on = fields.Datetime(readonly=True,
        help='When this audio was last moved to the archived state. Cleared '
             'when the audio leaves archived — so the value always reflects '
             'the most recent archival, not historical ones.')
    last_fallback_logged_on = fields.Datetime(
        readonly=True,
        help='When the TwiML audio() helper most recently posted a '
             'fallback.archived chatter notice on this audio. Used to '
             'suppress repeat posts within the fallback chatter window so '
             'high-traffic flows don\'t flood the log.')
    days_archived = fields.Integer(compute='_compute_days_archived',
        help='Full days since archive. Shown next to the Unarchive button so '
             'operators see the cooling-off window at a glance.')
    last_reachability_refresh_on = fields.Datetime(
        compute='_compute_reachability_stamp',
        string='Reachability Refreshed',
        help='Mirror of the singleton stamp on connect.settings — displayed '
             'on the audio form so operators know whether is_reachable is '
             'current when they make archive decisions.')
    reference_ids = fields.One2many('connect.audio.reference', 'audio_id',
        string='Where Used')
    reference_count = fields.Integer(compute='_compute_reference_counts', store=True)
    active_reference_count = fields.Integer(compute='_compute_reference_counts',
        store=True,
        help='Referrers whose own state would route a live call through this '
             'audio.')
    is_referenced = fields.Boolean(compute='_compute_reference_counts', store=True)
    has_active_reference = fields.Boolean(compute='_compute_reference_counts',
        store=True,
        help='True when at least one referrer is in an active state.')

    _unique_system_key = Constraint(
        'UNIQUE(system_key)',
        'system_key must be unique across all audios.',
    )
    _unique_uuid = Constraint(
        'UNIQUE(uuid)',
        'uuid must be unique across all audios.',
    )

    latest_utterance_id = fields.Many2one('connect.audio.utterance',
        compute='_compute_latest_utterance', store=True,
        string='Latest Utterance',
        help='Most recent utterance by generated_on — drives the preview '
             'widget in form views. Stored so related fields can reverse-walk '
             'dependencies when utterance.preview_audio recomputes.')
    latest_preview_audio = fields.Html(
        related='latest_utterance_id.preview_audio', string='Preview',
        sanitize=False,
        help='Inline HTML5 <audio> widget for the most recent utterance. '
             'Empty for source=twilio_tts since <Say> is rendered by '
             'Twilio during the call, not by us.')
    source_preview_audio = fields.Html(compute='_compute_source_preview_audio',
        string='Source Preview', sanitize=False,
        help='Inline <audio> player sourced directly from the raw payload '
             '(external URL or internal attachment) — independent of '
             'utterance caching, so it works before the audio is ever '
             'rendered for a call. Empty for TTS sources (see '
             'latest_preview_audio once a render exists) and for browser '
             'recordings (the recorder widget carries its own player).')

    @api.depends('utterance_ids')
    def _compute_utterance_count(self):
        for rec in self:
            rec.utterance_count = len(rec.utterance_ids)

    @api.depends('reference_ids', 'reference_ids.is_active', 'system_key')
    def _compute_reference_counts(self):
        for rec in self:
            rec.reference_count = len(rec.reference_ids)
            active = rec.reference_ids.filtered('is_active')
            rec.active_reference_count = len(active)
            rec.is_referenced = bool(rec.reference_count)
            # system_key audios are dispatched at runtime via tts_system_message —
            # no stored m2o, but they're always reachable. Treat as active.
            rec.has_active_reference = bool(rec.active_reference_count or rec.system_key)

    @api.depends('reference_ids', 'reference_ids.is_active',
                 'reference_ids.referrer_model_label', 'system_key')
    def _compute_reference_summary(self):
        """Produce a one-line digest readable at list/kanban density.

        Grouping by human-readable model label keeps the summary stable
        across locales and gives the operator a "who uses this" answer
        without opening the form.
        """
        for rec in self:
            parts = []
            if rec.system_key:
                parts.append(f'system: {rec.system_key}')
            by_label = {}
            for ref in rec.reference_ids:
                label = ref.referrer_model_label or ref.referrer_model
                counts = by_label.setdefault(label, [0, 0])
                counts[0] += 1
                if ref.is_active:
                    counts[1] += 1
            for label in sorted(by_label):
                total, active = by_label[label]
                plural = label if total == 1 else f'{label}s'
                if active == total:
                    parts.append(f'{total} {plural} (live)')
                elif active:
                    parts.append(f'{total} {plural} ({active} live)')
                else:
                    parts.append(f'{total} {plural} (inactive)')
            rec.reference_summary = ' · '.join(parts) if parts else 'unused'

    def _compute_reachability_stamp(self):
        stamp = self.env['connect.settings'].sudo().search(
            [], limit=1).last_reachability_refresh_on
        for rec in self:
            rec.last_reachability_refresh_on = stamp

    @api.depends('archived_on')
    def _compute_days_archived(self):
        now = fields.Datetime.now()
        for rec in self:
            if rec.archived_on:
                delta = now - rec.archived_on
                rec.days_archived = delta.days
            else:
                rec.days_archived = 0

    @api.depends('utterance_ids.served_on')
    def _compute_last_played_on(self):
        for rec in self:
            if rec.utterance_ids:
                served = [u.served_on for u in rec.utterance_ids if u.served_on]
                rec.last_played_on = max(served) if served else False
            else:
                rec.last_played_on = False

    @api.depends('utterance_ids.generated_on')
    def _compute_last_generated_on(self):
        for rec in self:
            if rec.utterance_ids:
                dates = [u.generated_on for u in rec.utterance_ids
                         if u.generated_on]
                rec.last_generated_on = max(dates) if dates else False
            else:
                rec.last_generated_on = False

    @api.depends('utterance_ids.generated_on')
    def _compute_latest_utterance(self):
        for rec in self:
            if rec.utterance_ids:
                rec.latest_utterance_id = rec.utterance_ids.sorted('generated_on', reverse=True)[0]
            else:
                rec.latest_utterance_id = False

    @api.depends('source', 'static_url',
                 'attachment_id', 'attachment_id.write_date')
    def _compute_source_preview_audio(self):
        for rec in self:
            src = None
            if rec.source == 'external_url' and rec.static_url:
                # User-supplied URL — escape so a quote in the path can't
                # break out of the src attribute (this Html field is
                # sanitize=False).
                src = escape(rec.static_url)
            elif rec.source == 'attachment' and rec.attachment_id:
                stamp = rec.attachment_id.write_date or ''
                src = (f'/web/content?model=ir.attachment'
                       f'&id={rec.attachment_id.id}'
                       f'&field=datas&download=false'
                       f'&unique={escape(str(stamp))}')
            if src:
                rec.source_preview_audio = (
                    f'<audio controls preload="auto" class="w-100" src="{src}"/>'
                )
            else:
                rec.source_preview_audio = False

    # ------------------------------------------------------------------
    # References — where is this audio actually used?
    # ------------------------------------------------------------------

    def _audio_referrers(self):
        """Hook: return [(model_name, field_name, is_active_callable)].

        Extend via _inherit to register new referrers — each module that
        points a Many2one at connect.audio should extend this so the audio
        shows up in the Where Used tab.

        `is_active_callable(record)` tells whether a live PSTN call would
        actually route through this audio given the referrer's current
        state (e.g. callout running vs. draft, user active vs. archived).
        Returning True means "yes, inbound or outbound calls hit this".
        Returning a string instead of False gives an explanation shown in
        the overview's inactive_reason column.

        Base registers connect.user and connect.callflow audio m2os — these
        fields live on base models now (were moved out of connect_elevenlabs
        in 1.14.0 as part of the text→audio migration).
        """
        return [
            ('connect.user', 'greeting_audio_id',
             lambda rec: True if rec.active else 'user archived'),
            ('connect.user', 'voicemail_audio_id',
             lambda rec: True if rec.active else 'user archived'),
            ('connect.callflow', 'prompt_audio_id',
             lambda rec: True if rec.active else 'callflow archived'),
            ('connect.callflow', 'invalid_input_audio_id',
             lambda rec: True if rec.active else 'callflow archived'),
            ('connect.callflow', 'voicemail_audio_id',
             lambda rec: True if rec.active else 'callflow archived'),
            ('connect.callflow', 'after_hours_audio_id',
             lambda rec: True if rec.active else 'callflow archived'),
            # Singleton settings row — always "active"; no archive state to
            # branch on. Surfaces in Where-Used so operators can see which
            # audio is currently serving as park hold music.
            ('connect.settings', 'park_hold_music_audio_id',
             lambda rec: True),
            # TwiML bodies reference audios via audio('<uuid>'), scanned
            # into the referenced_audio_ids M2m on save. A twiml without an
            # exten is orphaned (not wired to any extension); treat that
            # as inactive so the Where-Used row flags dead-prompt candidates.
            ('connect.twiml', 'referenced_audio_ids',
             lambda rec: True if rec.exten else 'twiml not wired to extension'),
        ]

    def _collect_references(self):
        """Walk _audio_referrers and return a list of ref-dicts for self.

        Each dict: audio_id, referrer_model, referrer_model_label,
        referrer_res_id, referrer_name, field_name, is_active, inactive_reason.

        Silently skips registered referrers whose model or field doesn't
        exist (uninstalled module, renamed field) — those are not errors.
        """
        self.ensure_one()
        rows = []
        for model_name, field_name, is_active_callable in self._audio_referrers():
            Model = self.env.get(model_name)
            if Model is None:
                continue
            if field_name not in Model._fields:
                continue
            # active_test=False so archived referrers appear in Where-Used
            # with inactive_reason='user archived' / 'callflow archived' —
            # the lambdas below expect to see them, and the default
            # active filter would silently drop archived rows.
            referrers = Model.sudo().with_context(active_test=False).search(
                [(field_name, '=', self.id)])
            if not referrers:
                continue
            model_label = self.env['ir.model']._get(model_name).name or model_name
            for ref in referrers:
                verdict = is_active_callable(ref)
                if verdict is True:
                    is_active, inactive_reason = True, False
                elif verdict is False or verdict is None:
                    is_active, inactive_reason = False, 'inactive'
                else:
                    # Callable returned a string — treat as a reason.
                    is_active, inactive_reason = False, str(verdict)
                rows.append({
                    'audio_id': self.id,
                    'referrer_model': model_name,
                    'referrer_model_label': model_label,
                    'referrer_res_id': ref.id,
                    'referrer_name': ref.display_name or f'{model_name}#{ref.id}',
                    'field_name': field_name,
                    'is_active': is_active,
                    'inactive_reason': inactive_reason,
                })
        return rows

    def _refresh_references(self):
        """Rebuild reference_ids for each audio in self. Idempotent.

        Deletes stale rows and creates missing ones per audio. Existing rows
        whose fields changed (is_active flipped, referrer renamed) are updated
        in place.
        """
        Reference = self.env['connect.audio.reference'].sudo()
        for audio in self:
            fresh = audio._collect_references()
            existing = Reference.search([('audio_id', '=', audio.id)])
            # Key existing rows by (referrer_model, referrer_res_id, field_name)
            # so we can match & update or unlink misses.
            fresh_keys = {(r['referrer_model'], r['referrer_res_id'], r['field_name'])
                          for r in fresh}
            stale = existing.filtered(
                lambda e: (e.referrer_model, e.referrer_res_id, e.field_name)
                          not in fresh_keys)
            if stale:
                stale.unlink()
            existing_by_key = {
                (e.referrer_model, e.referrer_res_id, e.field_name): e
                for e in existing - stale
            }
            to_create = []
            for row in fresh:
                key = (row['referrer_model'], row['referrer_res_id'], row['field_name'])
                hit = existing_by_key.get(key)
                if hit:
                    hit.write({
                        'referrer_model_label': row['referrer_model_label'],
                        'referrer_name': row['referrer_name'],
                        'is_active': row['is_active'],
                        'inactive_reason': row['inactive_reason'],
                    })
                else:
                    to_create.append(row)
            if to_create:
                Reference.create(to_create)
            audio._reconcile_state_with_references()

    def _reconcile_state_with_references(self):
        """Auto-flip reviewed ↔ live based on active references.

        Never touches draft (not approved yet) or archived (explicitly
        retired). Those require a manual action to leave.
        """
        self.ensure_one()
        if self.state not in ('reviewed', 'live'):
            return
        should_be_live = self.has_active_reference
        if should_be_live and self.state != 'live':
            self.state = 'live'
        elif not should_be_live and self.state != 'reviewed':
            self.state = 'reviewed'

    def _refresh_references_async(self):
        """Queue a deferred _refresh_references via queue_job. Falls back to
        inline refresh when queue_job is not installed — same outcome, just
        synchronous. Errors are swallowed with a warning so a bookkeeping
        miss never rolls back a caller's actual save."""
        if not self:
            return
        try:
            with_delay = getattr(self, 'with_delay', None)
            if with_delay:
                with_delay()._refresh_references()
            else:
                self._refresh_references()
        except Exception as e:
            logger.warning(
                'Audio reference refresh dispatch failed for %s: %s',
                self.ids, e)

    # ------------------------------------------------------------------
    # Reachability — may-reach BFS from routing entry points
    # ------------------------------------------------------------------

    @api.model
    def _reachability_entry_points(self):
        """Hook: return [(model_name, domain), ...] — origination points
        for calls. Base adds inbound DIDs; modules extend with their own
        originators (e.g. running outbound campaigns).

        Everything reachable from these seeds through _reachability_successors
        edges is considered "live" for the purpose of the is_reachable flag.
        """
        return [('connect.number', [])]

    @api.model
    def _reachability_successors(self, record):
        """Hook: given a record in the routing graph, return a list of
        (target_model, target_id) pairs representing the next hops a call
        could take from this record.

        Terminal leaves are (connect.audio, audio_id) — once the BFS lands
        on those, the audio is reachable. This is a may-reach analysis:
        when in doubt include the branch (all DTMF choices, all business-
        hour branches, both voicemail and ring paths, etc.).

        The base implementation handles connect.number (destination→user/
        callflow/twiml), connect.callflow (ring_users, choices→exten,
        voicemail leaves), connect.exten (polymorphic dst via model+res_id),
        connect.callflow_choice (its exten). Modules add edges by overriding
        this method via _inherit on connect.audio.
        """
        model = record._name
        if model == 'connect.number':
            # Polymorphic destination — only one of user/callflow/twiml is set.
            if record.destination == 'user' and record.user:
                return [('connect.user', record.user.id)]
            if record.destination == 'callflow' and record.callflow:
                return [('connect.callflow', record.callflow.id)]
            if record.destination == 'twiml' and record.twiml:
                return [('connect.twiml', record.twiml.id)]
            return []
        if model == 'connect.twiml':
            # TwiML bodies may cite any number of audios via the audio()
            # helper. referenced_audio_ids is a stored compute populated by
            # scanning the body on save — we just follow it here.
            return [('connect.audio', aid)
                    for aid in record.referenced_audio_ids.ids]
        if model == 'connect.user':
            succ = []
            if record.greeting_audio_id:
                succ.append(('connect.audio', record.greeting_audio_id.id))
            if record.voicemail_audio_id:
                succ.append(('connect.audio', record.voicemail_audio_id.id))
            return succ
        if model == 'connect.callflow':
            succ = []
            # Audio leaves — prompts and voicemail played by this callflow.
            if record.prompt_audio_id:
                succ.append(('connect.audio', record.prompt_audio_id.id))
            if record.invalid_input_audio_id:
                succ.append(('connect.audio', record.invalid_input_audio_id.id))
            if record.voicemail_audio_id:
                succ.append(('connect.audio', record.voicemail_audio_id.id))
            if record.after_hours_audio_id:
                succ.append(('connect.audio', record.after_hours_audio_id.id))
            # Simultaneous ring to users.
            for user in record.ring_users:
                succ.append(('connect.user', user.id))
            # Menu / DTMF / speech choices land on an exten, which is
            # polymorphic (user / callflow / twiml).
            for choice in record.choices:
                if choice.exten:
                    succ.append(('connect.exten', choice.exten.id))
            return succ
        if model == 'connect.callflow_choice':
            if record.exten:
                return [('connect.exten', record.exten.id)]
            return []
        if model == 'connect.exten':
            # Reference-field-equivalent: model (char) + res_id (int) form
            # the polymorphic pointer. Skip if either side is missing.
            if record.model and record.res_id:
                return [(record.model, record.res_id)]
            return []
        return []

    @api.model
    def _refresh_reachability(self):
        """Walk the routing graph from entry points, mark is_reachable on
        every connect.audio + connect.audio.reference accordingly.

        O(V + E) over the reachable subgraph; cycles are bounded by the
        visited set. Cheap at typical scale (hundreds of audios, a few
        hundred routing records) — well under a second.

        Convention for raw-SQL migrations: any post-migrate script that
        touches routing columns (connect_number.destination, callflow
        audio/voicemail fields, callout.status, etc.) must call
        `env['connect.audio']._refresh_reachability()` at the end, because
        raw SQL doesn't fire the referrer mixin that normally catches ORM
        writes. The post_init_hook on the connect module handles install
        and module-upgrade paths; per-migration calls cover the rest.
        """
        Audio = self.env['connect.audio'].sudo()
        Reference = self.env['connect.audio.reference'].sudo()

        visited = set()       # set of (model, id) pairs
        reachable_audios = set()
        worklist = []

        for model_name, domain in Audio._reachability_entry_points():
            Model = self.env.get(model_name)
            if Model is None:
                continue
            for rec in Model.sudo().search(domain):
                worklist.append((model_name, rec.id))

        while worklist:
            key = worklist.pop()
            if key in visited:
                continue
            visited.add(key)
            model_name, rec_id = key
            if model_name == 'connect.audio':
                reachable_audios.add(rec_id)
                # Audios are terminal leaves — don't walk further.
                continue
            Model = self.env.get(model_name)
            if Model is None:
                continue
            record = Model.sudo().browse(rec_id).exists()
            if not record:
                continue
            for succ in Audio._reachability_successors(record):
                if succ not in visited:
                    worklist.append(succ)

        # system_key audios are dispatched at runtime by code. No stored edge
        # leads to them, but every running callflow could trigger one. Treat
        # them as always reachable to avoid false-negative archive-temptation.
        system_ids = Audio.search([('system_key', '!=', False)]).ids
        reachable_audios.update(system_ids)

        # Apply in two batched writes regardless of recordset size. Filtering
        # first means we only touch rows whose flag actually changed — no
        # wasted UPDATE cycles and no recompute-cascade churn on a no-op.
        all_audios = Audio.search([])
        flip_on = all_audios.filtered(
            lambda a: a.id in reachable_audios and not a.is_reachable)
        flip_off = all_audios.filtered(
            lambda a: a.id not in reachable_audios and a.is_reachable)
        if flip_on:
            flip_on.write({'is_reachable': True})
        if flip_off:
            flip_off.write({'is_reachable': False})

        # Same batched-delta pattern for references. A reference is
        # reachable iff its referring record was visited AND the audio it
        # points at is in the reachable set.
        all_refs = Reference.search([])
        ref_flip_on = all_refs.filtered(
            lambda r: (r.referrer_model, r.referrer_res_id) in visited
                      and r.audio_id.id in reachable_audios
                      and not r.is_reachable)
        ref_flip_off = all_refs.filtered(
            lambda r: not ((r.referrer_model, r.referrer_res_id) in visited
                           and r.audio_id.id in reachable_audios)
                      and r.is_reachable)
        if ref_flip_on:
            ref_flip_on.write({'is_reachable': True})
        if ref_flip_off:
            ref_flip_off.write({'is_reachable': False})

        # Stamp the singleton settings row so the Overview can show "Last
        # refreshed N ago". Idempotent — we write even on no-op BFS passes
        # so the badge stays green when nothing needed flipping.
        settings = self.env['connect.settings'].sudo().search([], limit=1)
        if not settings:
            settings = self.env['connect.settings'].sudo().create({})
        settings.last_reachability_refresh_on = fields.Datetime.now()

    # Constant identity_key for reachability jobs: queue_job refuses to
    # enqueue a second pending job that shares this key, so bulk operations
    # (1000-row CSV import, mass archive) collapse to a single BFS run
    # instead of queueing N redundant jobs behind each other.
    _REACHABILITY_IDENTITY_KEY = 'connect_audio_refresh_reachability'

    def _refresh_reachability_async(self):
        """Queue the global BFS via queue_job, deduped by identity_key.

        When many routing writes happen in one transaction (CSV import, mass
        archive, bulk SQL patch followed by an ORM save), queue_job's
        identity_key keeps only one pending job so the BFS doesn't run N
        times for the same end state. Errors are logged, not raised — a
        bookkeeping miss must not roll back a caller's save.
        """
        Audio = self.env['connect.audio'].sudo()
        try:
            with_delay = getattr(Audio, 'with_delay', None)
            if with_delay:
                with_delay(identity_key=self._REACHABILITY_IDENTITY_KEY)\
                    ._refresh_reachability()
            else:
                Audio._refresh_reachability()
        except Exception as e:
            logger.warning('Reachability refresh dispatch failed: %s', e)

    def action_refresh_reachability(self):
        """Manual override for the Overview — rebuild from scratch and
        surface a confirmation toast with the new freshness stamp."""
        self.env['connect.audio'].sudo()._refresh_reachability()
        settings = self.env['connect.settings'].sudo().search([], limit=1)
        stamp = settings.last_reachability_refresh_on if settings else False
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'title': 'Reachability refreshed',
                'message': (f'Verified at {stamp}.' if stamp
                            else 'Reachability recomputed.'),
                'sticky': False,
            },
        }

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def action_mark_reviewed(self):
        for rec in self:
            if rec.state == 'archived':
                raise ValidationError(
                    f'Audio {rec.name!r} is archived; unarchive first.')
            rec.state = 'live' if rec.has_active_reference else 'reviewed'

    # ------------------------------------------------------------------
    # Create / write hooks — validate recorded audio masters. PSTN-format
    # (μ-law) bytes are derived lazily into connect.audio.utterance on
    # first render(), not baked into recording_file on save. The master
    # stays wideband so future consumers (HD WebRTC, Opus playback, provider
    # re-synthesis) can draw from the highest-quality source we have.
    # ------------------------------------------------------------------

    @api.model
    def _validate_recording_master(self, recording_value):
        """Validate an uploaded WAV parses; return the value UNCHANGED.

        Accepts base64 str or raw bytes. Replaces the in-place transcode
        pipeline: the master is preserved verbatim so every consumer draws
        from the original-quality source. μ-law-for-Twilio is derived on
        demand via _get_or_create_record_utterance.

        Also enforces the server-side size ceiling — the client-side cap in
        the JS widget is courtesy, the server is the trust boundary.
        """
        if not recording_value:
            return recording_value
        if isinstance(recording_value, str):
            try:
                wav_in = base64.b64decode(recording_value)
            except Exception as e:
                raise ValidationError(f'recording_file is not valid base64: {e}')
        else:
            wav_in = bytes(recording_value)
        # Parse-check so a corrupt upload fails at save time, not at first play.
        try:
            audio_ulaw._parse_wav(wav_in)
        except ValueError as e:
            raise ValidationError(f'recording_file is not a valid WAV: {e}')
        max_bytes = self._get_max_recording_bytes()
        if len(wav_in) > max_bytes:
            mb = len(wav_in) / 1024 / 1024
            cap_mb = max_bytes / 1024 / 1024
            raise ValidationError(
                f'Recording is too large ({mb:.1f} MB). '
                f'Maximum is {cap_mb:.1f} MB '
                f'(ir.config_parameter connect.audio.max_recording_bytes).')
        return recording_value

    @api.model
    def _get_max_recording_bytes(self):
        """Resolve the current server-side recording ceiling.

        Reads ir.config_parameter 'connect.audio.max_recording_bytes' with a
        DEFAULT_MAX_RECORDING_BYTES fallback. Invalid values log a warning
        and fall back to the default rather than raising — operator typos
        shouldn't break save paths.
        """
        param = self.env['ir.config_parameter'].sudo().get_param(
            'connect.audio.max_recording_bytes')
        if param:
            try:
                return int(param)
            except (ValueError, TypeError):
                logger.warning(
                    'Invalid ir.config_parameter value for '
                    'connect.audio.max_recording_bytes: %r. Using default.',
                    param)
        return DEFAULT_MAX_RECORDING_BYTES

    @api.model
    def _transcode_recording_for_pstn(self, recording_value):
        """Derive an 8 kHz mono μ-law WAV from a browser-uploaded master WAV.

        Accepts base64 str or raw bytes, returns base64. Idempotent — input
        already in target format round-trips unchanged. Raises ValidationError
        on unparseable input. This is NO LONGER called from create/write on
        the audio row (masters are preserved there); it's the derivation used
        by _get_or_create_record_utterance to produce cached μ-law utterances
        on demand.
        """
        if not recording_value:
            return recording_value
        if isinstance(recording_value, str):
            try:
                wav_in = base64.b64decode(recording_value)
            except Exception as e:
                raise ValidationError(f'recording_file is not valid base64: {e}')
        else:
            wav_in = bytes(recording_value)
        try:
            wav_out = audio_ulaw.transcode_to_ulaw_wav(wav_in)
        except ValueError as e:
            raise ValidationError(f'Recording transcode failed: {e}')
        return base64.b64encode(wav_out).decode('ascii')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('source') == 'record' and vals.get('recording_file'):
                # Preserve the master verbatim. We validate parseability and
                # size here; μ-law derivation happens lazily via the utterance
                # cache on first render().
                vals['recording_file'] = self._validate_recording_master(
                    vals['recording_file'])
                vals.setdefault('recording_mimetype', 'audio/wav')
        records = super().create(vals_list)
        # A fresh audio has no referrers yet — refreshing inline here just
        # initialises reference_ids = [] and reconciles state. Cheap, and
        # avoids spawning a queue_job for the no-op case.
        records._refresh_references()
        return records

    def unlink(self):
        sys_key = self.filtered('system_key')
        if sys_key:
            keys = ', '.join(sys_key.mapped('system_key'))
            raise ValidationError(f'System audio cannot be deleted: {keys}')
        return super().unlink()

    def write(self, vals):
        # --- UUID is the stable reference key for TwiML/TwiPy audio() calls.
        # Changing it would silently break every reference in every body. The
        # migration path sets allow_uuid_write=True; nothing else may cross.
        # The guard fires whenever the incoming uuid differs from the record's
        # current value — including the NULL-to-set path (a row with no uuid
        # yet must not be assigned a specific one outside the migration bypass,
        # since that would let callers mint reference keys that collide with
        # baked system UUIDs).
        if 'uuid' in vals and not self.env.context.get('allow_uuid_write'):
            for rec in self:
                if vals['uuid'] != rec.uuid:
                    raise ValidationError(
                        'connect.audio.uuid is immutable once set. Create a '
                        'new audio record if you need a different reference '
                        'key.')
        # --- Native archival gate (Action → Archive sets active=False) ---
        if vals.get('active') is False:
            sys_key = self.filtered('system_key')
            if sys_key:
                keys = ', '.join(sys_key.mapped('system_key'))
                raise ValidationError(
                    f'System audio cannot be archived: {keys}')
            with_active_refs = self.filtered(
                lambda r: r.active_reference_count > 0)
            if with_active_refs:
                names = ', '.join(with_active_refs.mapped('name'))
                raise ValidationError(
                    f'Remove all active references before archiving: {names}')
            vals = dict(vals)
            vals['state'] = 'archived'
            vals['archived_on'] = fields.Datetime.now()

        elif 'active' in vals and vals['active']:
            vals = dict(vals)
            vals['archived_on'] = False

        master_changing = False
        if vals.get('recording_file'):
            # Validate if this write either flips source to 'record' or the
            # existing records are already source='record'. Heterogeneous sets
            # keep their old behavior (no-op), which is safe because the
            # source-payload constraint rejects mismatched combinations.
            should_validate = (vals.get('source') == 'record'
                               or any(r.source == 'record' for r in self))
            if should_validate:
                vals = dict(vals)
                vals['recording_file'] = self._validate_recording_master(
                    vals['recording_file'])
                vals.setdefault('recording_mimetype', 'audio/wav')
                master_changing = True

        # Stamp / clear archived_on and keep active in sync with state
        # transitions that go through the state field directly (e.g. tests or
        # internal state-machine calls rather than the Action-menu path).
        if 'state' in vals:
            if vals['state'] == 'archived':
                vals = dict(vals)
                vals.setdefault('archived_on', fields.Datetime.now())
                vals.setdefault('active', False)
            elif any(r.state == 'archived' for r in self):
                # Leaving archived — clear timestamp and restore active.
                vals = dict(vals)
                vals.setdefault('archived_on', False)
                vals.setdefault('active', True)

        result = super().write(vals)

        # Master changed → invalidate cached μ-law derivatives.
        if master_changing:
            stale = self.utterance_ids.filtered(
                lambda u: u.source_used == 'record')
            if stale:
                stale.unlink()

        if {'system_key', 'source', 'active'} & vals.keys():
            self._refresh_references_async()

        # Post-unarchive: restore state out of 'archived' now that active=True
        # is written and has_active_reference is current.
        if 'active' in vals and vals.get('active'):
            was_archived = self.filtered(lambda r: r.state == 'archived')
            if was_archived:
                to_live = was_archived.filtered('has_active_reference')
                to_draft = was_archived - to_live
                if to_live:
                    to_live.write({'state': 'live'})
                if to_draft:
                    to_draft.write({'state': 'draft'})

        return result

    def action_refresh_references(self):
        """Manual 'Refresh' button on the audio form."""
        self._refresh_references()
        return True

    def action_open_references(self):
        """Smart-button action: open the Where-Used list filtered to this audio.

        Replaces the old global 'Audio Usage Overview' menu with a per-record
        entry point — operators look at where a single audio is used far more
        often than they browse the cross-audio BoM view.
        """
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Where {self.name or self.display_name!r} is used',
            'res_model': 'connect.audio.reference',
            'view_mode': 'list',
            'domain': [('audio_id', '=', self.id)],
            'context': {
                'default_audio_id': self.id,
                'search_default_group_audio': 0,
            },
        }

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @api.model
    def _dynamic_sources(self):
        """Sources that support {token} substitution and dynamic text.

        These are the TTS sources — synthesised at render time, so per-record
        templating is meaningful. Provider extensions override to union-in
        their own source keys (e.g. connect_elevenlabs adds 'elevenlabs_tts').
        """
        return {'twilio_tts'}

    @api.constrains('is_dynamic', 'source')
    def _check_dynamic_source(self):
        dynamic = self._dynamic_sources()
        for rec in self:
            if rec.is_dynamic and rec.source not in dynamic:
                raise ValidationError(
                    f'Dynamic audio is only supported for TTS sources '
                    f'({", ".join(sorted(dynamic))}). Got source={rec.source}.'
                )

    @api.constrains('is_dynamic', 'model_id', 'static_text')
    def _check_template_tokens(self):
        for rec in self:
            if not rec.is_dynamic or not rec.static_text:
                continue
            if not rec.model_id:
                raise ValidationError(
                    'Dynamic audio requires a model so {token} fields can be validated.'
                )
            unknown = rec._unknown_tokens(rec.static_text)
            if unknown:
                raise ValidationError(
                    f'Unknown field tokens for model {rec.model_id.model}: '
                    f'{", ".join(sorted(unknown))}'
                )

    @api.constrains('source', 'static_url', 'attachment_id', 'recording_file',
                    'recording_mimetype', 'static_text')
    def _check_source_payload(self):
        for rec in self:
            if rec.source == 'external_url':
                if not rec.static_url:
                    raise ValidationError('source=external_url requires static_url.')
                scheme = urlsplit(rec.static_url).scheme
                if scheme != 'https':
                    raise ValidationError(
                        f'static_url must use https (got scheme={scheme!r}). '
                        'Twilio media servers reject non-HTTPS URLs in production.'
                    )
            if rec.source == 'attachment':
                if not rec.attachment_id:
                    raise ValidationError('source=attachment requires attachment_id.')
                if rec.attachment_id.mimetype not in TWILIO_PLAYABLE_MIMETYPES:
                    raise ValidationError(
                        f'attachment_id.mimetype={rec.attachment_id.mimetype!r} is not '
                        f'playable by Twilio. Allowed: {sorted(TWILIO_PLAYABLE_MIMETYPES)}.'
                    )
            if rec.source == 'record':
                if not rec.recording_file:
                    raise ValidationError('source=record requires recording_file.')
                if rec.recording_mimetype not in TWILIO_PLAYABLE_MIMETYPES:
                    raise ValidationError(
                        f'recording_mimetype={rec.recording_mimetype!r} is not playable '
                        f'by Twilio. Allowed: {sorted(TWILIO_PLAYABLE_MIMETYPES)}.'
                    )
            if rec.source in rec._dynamic_sources() and not rec.static_text:
                raise ValidationError(f'source={rec.source} requires static_text.')

    @staticmethod
    def jinja_to_token(template, root_var):
        """Translate a Jinja-flavored template into the new {token} syntax.

        Returns (converted_text, fully_convertible). When fully_convertible is
        False, the template uses Jinja features the new substitution can't
        represent (filters, control flow, variables outside `root_var`); the
        caller should fall back to legacy pre-rendering and store as
        is_dynamic=False.
        """
        if not template:
            return template, True
        if '{%' in template:
            return template, False
        prefix = f'{root_var}.'
        out = []
        last = 0
        for m in _JINJA_VAR_RE.finditer(template):
            inner = m.group(1).strip()
            if not inner.startswith(prefix):
                return template, False
            path = inner[len(prefix):]
            if not _JINJA_TOKEN_RE.match(path):
                return template, False
            out.append(template[last:m.start()])
            out.append('{' + path + '}')
            last = m.end()
        out.append(template[last:])
        return ''.join(out), True

    def _precache(self, record=None):
        """Render and persist the utterance for (audio, voice, text). Idempotent.

        Designed to be invoked async via queue_job's with_delay() so that
        Twilio webhooks never block on a renderer call.
        """
        self.ensure_one()
        try:
            self.render(record=record)
        except Exception as e:
            logger.warning('precache failed for audio %s: %s', self.id, e)

    def _unknown_tokens(self, template):
        """Return tokens in `template` that don't resolve against self.model_id._fields.

        Supports dotted paths (e.g. partner_id.country_id.name) by walking comodel relations.
        """
        self.ensure_one()
        Model = self.env[self.model_id.model]
        unknown = set()
        for token in TOKEN_RE.findall(template):
            parts = token.split('.')
            current_fields = Model._fields
            valid = True
            for i, part in enumerate(parts):
                field = current_fields.get(part)
                if not field:
                    valid = False
                    break
                if i < len(parts) - 1:
                    if not field.comodel_name:
                        valid = False
                        break
                    current_fields = self.env[field.comodel_name]._fields
            if not valid:
                unknown.add(token)
        return unknown

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _resolve_template(self, record=None):
        """Substitute {field.path} tokens in static_text using `record`.

        Returns the static_text unchanged when not dynamic. Raises if dynamic
        and record is missing or of the wrong model.
        """
        self.ensure_one()
        text = self.static_text or ''
        if not self.is_dynamic:
            return text
        if not record:
            raise ValidationError(
                f'Audio {self.name!r} is dynamic and requires a record to render.'
            )
        if record._name != self.model_id.model:
            raise ValidationError(
                f'Audio {self.name!r} expects record of model {self.model_id.model}, '
                f'got {record._name}.'
            )

        def repl(match):
            token = match.group(1)
            value = record
            for part in token.split('.'):
                value = getattr(value, part, '')
                if value is False or value is None:
                    return ''
            return str(value)

        return TOKEN_RE.sub(repl, text)

    def _resolve_voice(self):
        """Return the voice for this audio.

        When use_default_voice is True, voice_id is ignored and the per-source
        database default wins (`_default_voice_for_source`). When False, the
        explicit voice_id is used, falling back to the per-source default only
        when voice_id is empty — so a row can be pinned to a specific voice
        that persists across settings changes.
        """
        self.ensure_one()
        if self.use_default_voice:
            return self._default_voice_for_source(self.source)
        if self.voice_id:
            return self.voice_id
        return self._default_voice_for_source(self.source)

    def _default_voice_for_source(self, source):
        """Return the DB-wide default connect.voice for the given source.

        Twilio reads settings.default_twilio_voice. Other providers extend
        via _inherit (e.g. connect_elevenlabs returns settings.elevenlabs_voice).
        Returns an empty recordset when no default is configured — callers
        fall back to DEFAULT_TWILIO_VOICE in tts_mixin.
        """
        if source == 'twilio_tts':
            settings = self.env['connect.settings'].sudo().search([], limit=1)
            if settings and settings.default_twilio_voice:
                return settings.default_twilio_voice
        return self.env['connect.voice']

    @api.depends('source', 'use_default_voice', 'voice_id')
    def _compute_resolved_voice(self):
        for rec in self:
            rec.resolved_voice_id = rec._resolve_voice()

    def _renderers(self):
        """Return dict of source_key -> renderer method name. Override to add sources."""
        return {
            'record': '_render_record',
            'twilio_tts': '_render_twilio_tts',
            'external_url': '_render_external_url',
            'attachment': '_render_attachment',
        }

    def _record_target_params(self):
        """Hook: target format for source='record' utterance derivation.

        Today: Twilio PSTN => 8 kHz μ-law WAV. Override in WebRTC / Opus
        consumers to return a different derivative (e.g. 16 kHz Opus in OGG).
        The returned dict is hashed into the utterance cache's params_hash,
        so distinct target params produce distinct cached utterances.
        """
        self.ensure_one()
        return {
            'target_codec': 'mulaw',
            'target_rate': 8000,
            'target_container': 'wav',
        }

    def _get_or_create_record_utterance(self, target_params):
        """Lazy μ-law derivation from the stored master WAV.

        Cache key: (audio_id, voice=NULL, text_hash='', params_hash). voice_id
        is deliberately excluded from the key — record-source audio is
        voice-agnostic, so including voice would fragment the cache per voice
        while producing byte-identical files.
        """
        import hashlib
        import json
        self.ensure_one()
        if self.source != 'record':
            raise ValidationError(
                f'_get_or_create_record_utterance: audio {self.id} is '
                f'source={self.source!r}, not record.')
        if not self.recording_file:
            raise ValidationError('recording_file is empty.')

        Utterance = self.env['connect.audio.utterance'].sudo()
        text_hash = Utterance.hash_text('')
        params_hash = Utterance.hash_params(target_params)

        cached = Utterance.search([
            ('audio_id', '=', self.id),
            ('voice_id', '=', False),
            ('text_hash', '=', text_hash),
            ('params_hash', '=', params_hash),
        ], limit=1)
        if cached:
            return cached

        # Serialise concurrent derivations of the same (audio, target-params)
        # key to avoid duplicate transcode work under load.
        lock_key = int.from_bytes(
            hashlib.blake2b(
                f'rec:{self.id}:{params_hash}'.encode(), digest_size=8).digest(),
            'big', signed=True)
        self.env.cr.execute('SELECT pg_advisory_xact_lock(%s)', (lock_key,))

        cached = Utterance.search([
            ('audio_id', '=', self.id),
            ('voice_id', '=', False),
            ('text_hash', '=', text_hash),
            ('params_hash', '=', params_hash),
        ], limit=1)
        if cached:
            return cached

        codec = target_params.get('target_codec')
        rate = target_params.get('target_rate')
        container = target_params.get('target_container', 'wav')
        if codec == 'mulaw' and rate == 8000 and container == 'wav':
            derived_b64 = self._transcode_recording_for_pstn(self.recording_file)
            mimetype = 'audio/wav'
            filename = f'{uuid_lib.uuid4().hex}.wav'
        else:
            raise ValidationError(
                f'Unsupported target_params for record derivation: '
                f'{target_params!r}')

        try:
            return Utterance.create({
                'audio_id': self.id,
                'voice_id': False,
                'rendered_text': '',
                'text_hash': text_hash,
                'params': json.dumps(target_params, sort_keys=True),
                'params_hash': params_hash,
                'source_used': 'record',
                'file': derived_b64,
                'filename': filename,
                'mimetype': mimetype,
            })
        except IntegrityError:
            self.env.cr.rollback()
            return Utterance.search([
                ('audio_id', '=', self.id),
                ('voice_id', '=', False),
                ('text_hash', '=', text_hash),
                ('params_hash', '=', params_hash),
            ], limit=1)

    def render(self, record=None):
        """Resolve to a connect.audio.utterance, generating it if needed.

        The cache key is (audio, voice, rendered_text, provider_params). To
        learn the provider params we have to ask the renderer first — they
        depend on settings/voice config. So the flow is:
          1. Render the template + resolve the voice.
          2. Ask the renderer for its current params (no API call yet).
          3. Look up the cache by (audio, voice, text_hash, params_hash).
          4. On miss, take a PG advisory lock, re-check, then dispatch.

        source='record' short-circuits into _get_or_create_record_utterance:
        recordings don't go through the text/voice/params flow — the master
        WAV is the canonical input, parameterised by target codec/rate/
        container via _record_target_params().
        """
        import hashlib
        self.ensure_one()
        if self.source == 'record':
            return self._get_or_create_record_utterance(
                self._record_target_params())
        Utterance = self.env['connect.audio.utterance'].sudo()
        rendered = self._resolve_template(record)
        voice = self._resolve_voice()
        text_hash = Utterance.hash_text(rendered)
        params = self._renderer_params(voice)
        params_hash = Utterance.hash_params(params)

        cached = self._lookup_utterance(rendered, voice, text_hash, params_hash)
        if cached:
            return cached

        lock_key = int.from_bytes(
            hashlib.blake2b(
                f'{self.id}:{voice.id if voice else 0}:{text_hash}:{params_hash}'.encode(),
                digest_size=8,
            ).digest(),
            'big', signed=True,
        )
        self.env.cr.execute('SELECT pg_advisory_xact_lock(%s)', (lock_key,))

        cached = self._lookup_utterance(rendered, voice, text_hash, params_hash)
        if cached:
            return cached

        renderer_name = self._renderers().get(self.source)
        if not renderer_name:
            raise ValidationError(f'No renderer registered for source={self.source}.')
        payload = getattr(self, renderer_name)(rendered, voice)

        import json
        vals = {
            'audio_id': self.id,
            'voice_id': voice.id if voice else False,
            'rendered_text': rendered,
            'text_hash': text_hash,
            'params': json.dumps(params, sort_keys=True) if params else False,
            'params_hash': params_hash,
            'source_used': self.source,
        }
        vals.update(payload or {})
        try:
            return Utterance.create(vals)
        except IntegrityError:
            self.env.cr.rollback()
            cached = self._lookup_utterance(rendered, voice, text_hash, params_hash)
            if cached:
                return cached
            raise

    def _lookup_utterance(self, rendered, voice, text_hash, params_hash):
        Utterance = self.env['connect.audio.utterance'].sudo()
        return Utterance.search([
            ('audio_id', '=', self.id),
            ('text_hash', '=', text_hash),
            ('voice_id', '=', voice.id if voice else False),
            ('params_hash', '=', params_hash),
        ], limit=1)

    def _renderer_params(self, voice):
        """Return a dict of provider-specific params that affect the rendered
        output. Used to scope the cache key. Renderers that have no tunable
        parameters return {}.
        """
        self.ensure_one()
        return {}

    # ------------------------------------------------------------------
    # Built-in renderers
    # Each returns a dict of fields to merge into the utterance create vals.
    # File-bearing renderers must set: file (b64), filename, mimetype.
    # Returning {} produces a file-less utterance (callers fall back to <Say>).
    # ------------------------------------------------------------------

    def _render_record(self, rendered_text, voice):
        self.ensure_one()
        if not self.recording_file:
            raise ValidationError('Cannot render: recording_file is empty.')
        # By the time we reach this renderer the payload has been transcoded
        # to RIFF-wrapped 8 kHz μ-law WAV (see _transcode_recording_for_pstn).
        # Fallback filename/mimetype must reflect actual bytes — a .webm/
        # audio/webm fallback would produce an unplayable utterance on rows
        # with missing metadata.
        return {
            'file': self.recording_file,
            'filename': self.recording_filename or f'{uuid_lib.uuid4().hex}.wav',
            'mimetype': self.recording_mimetype or 'audio/wav',
        }

    def _render_twilio_tts(self, rendered_text, voice):
        # No file: callers branch on `if utterance.file: response.play(url) else response.say(text)`.
        return {}

    def _render_external_url(self, rendered_text, voice):
        self.ensure_one()
        if not self.static_url:
            raise ValidationError('Cannot render: static_url is empty.')
        # Percent-encode the path so spaces/unicode don't trip Twilio's fetcher,
        # while preserving the URL structure (scheme, host, query).
        parts = urlsplit(self.static_url)
        if parts.scheme != 'https':
            raise ValidationError(
                f'static_url must use https (got {parts.scheme!r}).'
            )
        safe_path = quote(parts.path, safe='/')
        normalized = f'{parts.scheme}://{parts.netloc}{safe_path}'
        if parts.query:
            normalized += f'?{parts.query}'
        # Probe the remote to validate reachability and cache its Content-Type.
        # Fail at render time so operators see misconfigurations immediately
        # instead of at first-call — and so the cached utterance row carries
        # the real mimetype for downstream decoding.
        mimetype = self._probe_external_url_mimetype(normalized)
        return {
            'filename': normalized,
            'mimetype': mimetype,
        }

    @api.model
    def _probe_external_url_mimetype(self, url):
        """HEAD-probe an external audio URL, return its Content-Type.

        Falls back to a Range-GET on 405 (some servers reject HEAD). Raises
        ValidationError on 4xx/5xx or connection failures so the operator
        sees a clear save error. Allows a missing Content-Type through with
        a warning — rare but valid on minimal static hosts. Rejects content
        types outside TWILIO_PLAYABLE_MIMETYPES so the save can't commit a
        URL Twilio will refuse to play.
        """
        import requests
        try:
            resp = requests.head(url, allow_redirects=True, timeout=5.0)
            if resp.status_code == 405:
                # Some servers reject HEAD; a tiny Range-GET is the standard
                # fallback and costs one byte.
                resp = requests.get(url, headers={'Range': 'bytes=0-0'},
                                    allow_redirects=True, timeout=5.0,
                                    stream=True)
                resp.close()
            if resp.status_code >= 400:
                raise ValidationError(
                    f'External audio URL returned HTTP {resp.status_code}. '
                    f'Twilio will fail to play this URL.')
        except requests.exceptions.RequestException as e:
            raise ValidationError(
                f'External audio URL is not reachable: '
                f'{type(e).__name__}: {e}')
        content_type = resp.headers.get('Content-Type', '').split(
            ';', 1)[0].strip().lower()
        if not content_type:
            logger.warning(
                'External URL %r returned no Content-Type; allowing through. '
                'Twilio may still play based on byte-sniffing.', url)
            return False
        if content_type not in TWILIO_PLAYABLE_MIMETYPES:
            raise ValidationError(
                f'External URL Content-Type {content_type!r} is not in the '
                f'set Twilio will play: '
                f'{sorted(TWILIO_PLAYABLE_MIMETYPES)}.')
        return content_type

    def _render_attachment(self, rendered_text, voice):
        self.ensure_one()
        if not self.attachment_id:
            raise ValidationError('Cannot render: attachment_id is empty.')
        att = self.attachment_id
        return {
            'file': att.datas,
            'filename': att.name,
            'mimetype': att.mimetype,
        }

    # ------------------------------------------------------------------
    # Playback helper
    # ------------------------------------------------------------------

    def play_on(self, response, record=None):
        """Render, then attach to a TwiML VoiceResponse/Gather.

        For file-bearing utterances calls response.play(url); for external_url
        rows the URL is in utterance.filename and we play it directly; otherwise
        we emit a <Say> with the voice's external_id (Twilio voice name) and
        fall back to the configured default.

        The <Say> fallback runs the rendered text through
        connect.settings.process_pronunciation so operator-configured SSML
        <sub alias="..."> substitutions (e.g. "3CHI" → "3-chee") still apply
        when no pre-synthesised audio file exists — matching what the legacy
        tts_say path did before the audio-library refactor.
        """
        self.ensure_one()
        utterance = self.render(record=record)
        if utterance.file:
            response.play(utterance.get_url())
        elif utterance.source_used == 'external_url' and utterance.filename:
            response.play(utterance.filename)
        else:
            voice_name = (utterance.voice_id.external_id
                          if utterance.voice_id and utterance.voice_id.provider == 'twilio'
                          else DEFAULT_TWILIO_VOICE)
            text = utterance.rendered_text or ''
            processed = self.env['connect.settings'].sudo().process_pronunciation(text)
            response.say(processed, voice=voice_name)
        return utterance

    def get_play_url(self, record=None):
        """Return an absolute URL Twilio can fetch to play this audio.

        Use this for contexts that need a media URL (not TwiML) — e.g.
        <Conference waitUrl="...">, which Twilio loops automatically when
        the response is audio/*.

        Returns None for source=twilio_tts: live <Say> has no pre-rendered
        media URL, and wrapping TTS in a TwiML endpoint for waitUrl is out
        of scope. Callers fall back to a provider-hosted hold loop in that
        case.
        """
        self.ensure_one()
        if self.source == 'external_url':
            return self.static_url or None
        if self.source in ('record', 'attachment'):
            utterance = self.render(record=record)
            return utterance.get_url()
        return None
