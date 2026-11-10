import base64
import io
import logging
import os
import re
import wave
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from tempfile import NamedTemporaryFile
from urllib.parse import quote

import requests
from markupsafe import escape
from odoo import _, fields, models, api, release, SUPERUSER_ID
from odoo.exceptions import AccessError, UserError, ValidationError

logger = logging.getLogger(__name__)

# A transcript with fewer words than this is not worth summarizing: the model
# only answers "the transcript is too short to summarize", which then lands in
# every linked chatter as noise.
MIN_SUMMARY_WORDS = 8
TIMESTAMP_RE = re.compile(r'\b\d{1,2}:\d{2}:\d{2}\b')
# _redact_stored_audio writes this at the start of its note when it silenced
# the kept copy. The carrier delete waits for it whenever the call has marks.
REDACTED_NOTE_PREFIX = 'Silenced '
PROVIDER_DELETE_BATCH = 20
# Attempts before a carrier delete stops retrying on its own. The retry action
# starts the count again.
PROVIDER_DELETE_ATTEMPTS = 5


def silence_wav_spans(wav_bytes, spans):
    """Zero the samples that fall inside ``spans``.

    Each span is ``(start_ms, end_ms)``. ``end_ms`` of None runs to the end
    of the file. The result is a wav with the same parameters.
    """
    source = io.BytesIO(wav_bytes)
    with wave.open(source, 'rb') as reader:
        params = reader.getparams()
        frames = bytearray(reader.readframes(reader.getnframes()))
    width = params.sampwidth * params.nchannels
    rate = params.framerate or 1
    total = len(frames)

    def _byte(ms):
        if ms is None:
            return total
        sample = int(rate * (ms / 1000.0))
        return max(0, min(total, sample * width))

    for start_ms, end_ms in spans:
        start = _byte(start_ms)
        end = _byte(end_ms)
        if end <= start:
            continue
        start -= start % width
        frames[start:end] = b'\x00' * (end - start)
    out = io.BytesIO()
    with wave.open(out, 'wb') as writer:
        writer.setparams(params)
        writer.writeframes(bytes(frames))
    return out.getvalue()


class Recording(models.Model):
    _name = 'connect.recording'
    _description = 'Recording'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'id'
    _order = 'id desc'

    _TRANSCRIPTION_MODEL = 'whisper-1'
    _TRANSCRIPTION_PRICE_PER_MINUTE = Decimal('0.006')
    _TRANSCRIPTION_PRICE_QUANTUM = Decimal('0.000001')

    call = fields.Many2one('connect.call', ondelete='set null')
    channel = fields.Many2one('connect.channel', ondelete='set null')
    partner = fields.Many2one('res.partner', ondelete='set null')
    sid = fields.Char('SID', readonly=True)
    call_sid = fields.Char(string='Channel SID', readonly=True)
    caller_user = fields.Many2one(related='call.caller_user', store=True, readonly=False)
    called_user = fields.Many2one('res.users', ondelete='set null')
    users = fields.Many2many('res.users', compute='_compute_users', string='Users')
    caller_number = fields.Char()
    called_number = fields.Char()
    media_url = fields.Char()
    recording_filename = fields.Char(readonly=True)
    recording_attachment = fields.Binary(attachment=True, readonly=True)
    price = fields.Char()
    price_unit = fields.Char()
    source = fields.Char()
    duration = fields.Integer()
    duration_human = fields.Char(compute='_get_duration_human')
    start_time = fields.Datetime()
    status = fields.Char()
    if release.version_info[0] >= 17.0:
        recording_widget = fields.Html(compute='_get_recording_widget', string='Recording', sanitize=False)
    else:
        recording_widget = fields.Char(compute='_get_recording_widget', string='Recording')
    transcript = fields.Text()
    transcription_token = fields.Char()
    transcription_error = fields.Char()
    # Work-queue flag for the transcription cron. Set on create() when
    # transcript_calls is enabled; the cron picks these up out of the
    # request path (see _cron_transcribe_recordings).
    transcription_pending = fields.Boolean(default=False, copy=False)
    transcription_price = fields.Char()
    summary = fields.Html()
    list_view_summary = fields.Html(compute='_get_list_view_summary')
    redaction_note = fields.Char(
        help='What happened when this copy was aligned to the recording marks.',
    )
    provider_deleted_at = fields.Datetime(
        string='Removed from carrier', readonly=True, copy=False,
        help='When the carrier confirmed its copy is gone. Set only after our '
             'archived copy read back intact.',
    )
    provider_delete_pending = fields.Boolean(
        string='Carrier delete pending', readonly=True, copy=False, index=True,
        help='The carrier copy is deleted once our archived copy is verified.',
    )
    provider_delete_attempts = fields.Integer(readonly=True, copy=False)
    provider_delete_error = fields.Char(
        string='Carrier copy kept', readonly=True, copy=False,
        help='Why the carrier still holds this recording.',
    )

    def _redaction_spans(self):
        self.ensure_one()
        marks = self.env['connect.recording.mark'].sudo().search([
            ('call', '=', self.call.id),
        ]) if self.call else self.env['connect.recording.mark']
        if self.channel:
            marks = marks.filtered(
                lambda mark: not mark.channel or mark.channel == self.channel)
        duration_ms = (self.duration or 0) * 1000 or None
        anchor = self.start_time or (
            self.channel.create_date if self.channel else None)

        class _Point:
            def __init__(self, kind, offset_ms):
                self.kind = kind
                self.offset_ms = offset_ms

        points = []
        for mark in marks:
            offset = mark.offset_ms or 0
            if anchor and mark.occurred_at:
                offset = max(0, int((mark.occurred_at - anchor).total_seconds() * 1000))
            points.append(_Point(mark.kind, offset))
        return self.env['connect.recording.mark'].spans_ms(points, duration_ms)

    def _redact_stored_audio(self):
        """Silence pause and stop spans on the copy we keep.

        Provider pause is attempted separately. This pass is what makes the
        copy match the marks when the provider kept recording.
        """
        for rec in self:
            spans = rec._redaction_spans()
            if not spans:
                continue
            payload = b''
            if rec.recording_attachment:
                payload = base64.b64decode(rec.recording_attachment)
            elif rec.media_url:
                if not self.env['connect.settings'].sudo()._is_trusted_media_url(rec.media_url):
                    logger.warning(
                        'Recording %s not fetched for redaction: media host is not trusted', rec.id)
                    continue
                try:
                    response = requests.get(rec.media_url, timeout=30)
                    response.raise_for_status()
                    payload = response.content
                except Exception as error:
                    rec.with_context(recording_redaction=True).write({
                        'redaction_note': 'Could not fetch audio to redact: {}'.format(error)[:200],
                    })
                    continue
            if not payload or not payload.startswith(b'RIFF'):
                if payload:
                    rec.with_context(recording_redaction=True).write({
                        'redaction_note': 'Audio is not wav; marks are kept for a later redact.',
                    })
                continue
            try:
                redacted = silence_wav_spans(payload, spans)
            except wave.Error as error:
                rec.with_context(recording_redaction=True).write({
                    'redaction_note': 'Could not read wav: {}'.format(error)[:200],
                })
                continue
            rec.with_context(recording_redaction=True).write({
                'recording_attachment': base64.b64encode(redacted),
                'redaction_note': '{}{} span(s) on the kept copy.'.format(
                    REDACTED_NOTE_PREFIX, len(spans)),
            })

    def write(self, vals):
        result = super().write(vals)
        if not self.env.context.get('recording_redaction'):
            watched = {'recording_attachment', 'media_url', 'start_time', 'duration', 'status'}
            if watched.intersection(vals):
                self._redact_stored_audio()
        if {'recording_attachment', 'media_url', 'status'}.intersection(vals):
            self._queue_provider_delete()
        return result

    # Carrier copy. A recording is pulled into our archive, verified there, and
    # only then deleted at the carrier. The archive is the Odoo attachment here;
    # a storage module (connect_s3) answers for its own backend through the
    # _archive_* seams. A provider module answers for its own carrier through
    # _provider_owns_recording and _provider_delete_recording.

    @api.model
    def _provider_delete_enabled(self):
        if self.env['ir.config_parameter'].sudo().get_param('database.is_neutralized'):
            return False
        return bool(self.env['connect.settings'].sudo().get_param('delete_provider_recording'))

    def _archive_mode(self):
        """Which archive holds our copy: 'attachment', a storage module's key, or None.

        None means the recording is kept at the carrier by choice
        (recording_storage = 'twilio'), so nothing may be deleted there.
        """
        storage = self.env['connect.settings'].sudo().get_param('recording_storage')
        return 'attachment' if storage == 'odoo_filestore' else None

    def _archive_queueable(self):
        """True when the archive step for this recording can start or has finished."""
        self.ensure_one()
        return self._archive_mode() == 'attachment'

    def _provider_owns_recording(self):
        """True when an installed provider can delete this recording at its carrier.

        Provider modules override this and answer True only for their own
        recordings. A carrier without a delete API never answers True.
        """
        self.ensure_one()
        return False

    def _provider_delete_recording(self):
        """Delete the carrier copy and return True once the carrier says it is gone.

        A 404 counts as gone. Any other outcome raises, so an unknown result
        is never recorded as a delete. Provider modules override this.
        """
        self.ensure_one()
        raise UserError(_('No installed provider can delete this recording at its carrier.'))

    def _queue_provider_delete(self):
        if not self or not self._provider_delete_enabled():
            return
        todo = self.filtered(
            lambda rec: not rec.provider_deleted_at
            and not rec.provider_delete_pending
            and rec.status == 'completed'
            and rec.sid
            and rec._provider_owns_recording()
            and rec._archive_queueable())
        if todo:
            todo.write({'provider_delete_pending': True, 'provider_delete_error': False})

    def _ingest_archive_copy(self):
        """Pull the carrier audio into the attachment when that is the archive.

        Returns True when it wrote the attachment. The write runs the
        redaction pass, so the stored copy is the one later verified.
        """
        self.ensure_one()
        if self._archive_mode() != 'attachment' or self.recording_attachment:
            return False
        path = self._download_recording_audio()
        try:
            with open(path, 'rb') as media:
                content = media.read()
        finally:
            os.unlink(path)
        if not content:
            raise ValueError('The carrier returned an empty recording')
        extension = '.wav' if content.startswith(b'RIFF') else '.mp3'
        self.write({
            'recording_attachment': base64.b64encode(content),
            'recording_filename': self.recording_filename or '{}{}'.format(
                self.sid or self.id, extension),
        })
        return True

    def _verify_archive_copy(self):
        """Raise unless our archived copy is stored and reads back intact."""
        self.ensure_one()
        if self._archive_mode() != 'attachment':
            raise UserError(_('No archive of ours holds this recording.'))
        attachment = self.env['ir.attachment'].sudo().search([
            ('res_model', '=', self._name),
            ('res_field', '=', 'recording_attachment'),
            ('res_id', '=', self.id),
        ], limit=1)
        # Read the stored file again, not the bytes this transaction wrote.
        attachment.invalidate_recordset()
        if not attachment or not attachment.checksum:
            raise ValueError('The stored recording has no attachment checksum')
        content = attachment.raw
        if not content:
            raise ValueError('The stored recording reads back empty')
        if attachment._compute_checksum(content) != attachment.checksum:
            raise ValueError('The stored recording does not match its checksum')

    def _redaction_settled(self):
        """False while the call has pause or stop marks the kept copy has not silenced."""
        self.ensure_one()
        if not self._redaction_spans():
            return True
        return (self.redaction_note or '').startswith(REDACTED_NOTE_PREFIX)

    def _delete_at_provider(self):
        """Verify our copy, then delete the carrier copy. Raises on any doubt."""
        self.ensure_one()
        if self.provider_deleted_at:
            return True
        if not self._redaction_settled():
            raise UserError(_(
                'Pause and stop marks are not yet silenced on the kept copy.'))
        self._verify_archive_copy()
        if not self._provider_delete_recording():
            raise UserError(_('The carrier did not confirm the delete.'))
        self.write({
            'provider_deleted_at': fields.Datetime.now(),
            'provider_delete_pending': False,
            'provider_delete_error': False,
        })
        return True

    @api.model
    def _cron_delete_provider_recordings(self, limit=PROVIDER_DELETE_BATCH):
        """Archive, verify, then delete queued recordings at their carrier.

        Each step commits before the next starts: the attachment is durable
        before it is verified, and the carrier is asked to delete only a copy
        that a committed archive holds.
        """
        if not self._provider_delete_enabled():
            return
        cron = self.env['ir.cron']
        pending = self.search([
            ('provider_delete_pending', '=', True),
            ('provider_deleted_at', '=', False),
        ], order='provider_delete_attempts, id', limit=limit)
        for rec in pending:
            try:
                with self.env.cr.savepoint():
                    ingested = rec._ingest_archive_copy()
                if ingested:
                    cron._commit_progress(0)
                with self.env.cr.savepoint():
                    rec._delete_at_provider()
            except Exception as error:
                logger.warning(
                    'Carrier copy kept for recording %s: %s', rec.id, error)
                attempts = rec.provider_delete_attempts + 1
                rec.write({
                    'provider_delete_attempts': attempts,
                    'provider_delete_pending': attempts < PROVIDER_DELETE_ATTEMPTS,
                    'provider_delete_error': (str(error) or type(error).__name__)[:250],
                })
            if not cron._commit_progress(1):
                break

    def action_retry_provider_delete(self):
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(_('Only administrators can retry a carrier delete.'))
        todo = self.filtered(lambda rec: not rec.provider_deleted_at)
        todo.write({
            'provider_delete_pending': False,
            'provider_delete_attempts': 0,
            'provider_delete_error': False,
        })
        todo._queue_provider_delete()
        return True

    @api.depends(
        'caller_user',
        'called_user',
        'call.caller_user',
        'call.called_users',
        'call.answered_user',
    )
    def _compute_users(self):
        for rec in self:
            users = rec.caller_user | rec.called_user
            if rec.call:
                users |= (
                    rec.call.caller_user
                    | rec.call.called_users
                    | rec.call.answered_user
                )
            rec.users = users

    def _fetch_media_to(self, temp_file):
        """Write this recording's audio into an open binary file object.

        Seam: storage add-ons (connect_twilio's S3 storage) override this to
        read the audio from their own backend instead of the provider's URL.
        """
        self.ensure_one()
        if self.recording_attachment:
            # Providers whose recording downloads require API auth
            # store the audio bytes on the record instead of
            # exposing a public media_url (e.g. connect_infobip,
            # Asterisk or LiveKit sidecars).
            temp_file.write(base64.b64decode(self.recording_attachment))
            return
        # Bounded download: media_url points at the provider's
        # recording store; without a timeout a hung endpoint
        # pins the worker.
        url = self._get_media_download_url()
        if not self.env['connect.settings'].sudo()._is_trusted_media_url(url):
            raise UserError(_("Recording media host is not a trusted provider host."))
        auth = self.env['connect.settings'].sudo()._get_media_auth(url)
        with requests.get(url, auth=auth, stream=True, timeout=30) as response:
            response.raise_for_status()
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    temp_file.write(chunk)

    def _download_recording_audio(self):
        """Materialize the shared media seam for file-based consumers.

        The caller owns the returned file. Failed downloads leave no partial
        file behind. Storage overrides belong on _fetch_media_to so portal,
        synchronous transcription and chunked transcription read the same bytes.
        """
        self.ensure_one()
        suffix = os.path.splitext(self.recording_filename or '')[1] or '.wav'
        path = None
        try:
            with NamedTemporaryFile(delete=False, suffix=suffix) as media:
                path = media.name
                self._fetch_media_to(media)
            return path
        except Exception:
            if path and os.path.exists(path):
                os.unlink(path)
            raise

    def transcribe_recording(self, openai_api_key, summary_prompt):
        result = {}
        temp_file_path = None
        try:
            client = self.env['connect.settings'].get_openai_client()
            # OpenAI infers the audio container from the file extension, so
            # keep the original one when the filename is known.
            suffix = os.path.splitext(self.recording_filename or '')[1] or '.mp3'
            with NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
                self._fetch_media_to(temp_file)
                temp_file_path = temp_file.name
            with open(temp_file_path, 'rb') as audio_file:
                transcript = client.audio.transcriptions.create(
                    model=self._get_transcription_model(), file=audio_file,
                    response_format='verbose_json', timestamp_granularities=["segment"])
            result['transcription_price'] = self._get_transcription_price(transcript)
            segments = ''
            for s in transcript.segments:
                seconds = int(s.start)
                ts = f"{int(seconds // 3600):02d}:{int((seconds % 3600) // 60):02d}:{int(seconds % 60):02d}"
                segments += '{} {}\n'.format(ts, s.text)
            result['transcript'] = segments
            if self._is_summarizable(segments):
                result.update(self.make_summary(client, summary_prompt, segments))
            result.setdefault('transcription_error', False)
        except Exception as e:
            logger.exception(f'Transcribe error: {e}')
            result['transcription_error'] = str(e)
        finally:
            # NamedTemporaryFile(delete=False) is not auto-removed; drop the
            # downloaded audio so /tmp does not grow without bound.
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.unlink(temp_file_path)
                except OSError:
                    logger.warning('Could not remove temp file %s', temp_file_path)
            result['transcription_pending'] = False
            self.write(result)
            self._delete_after_successful_transcription()

    @api.model
    def _format_transcription_price(self, price):
        if price is None or price is False:
            return False
        try:
            value = Decimal(str(price)).quantize(
                self._TRANSCRIPTION_PRICE_QUANTUM,
                rounding=ROUND_HALF_UP,
            )
        except (InvalidOperation, TypeError, ValueError):
            logger.warning('Invalid transcription price: %r', price)
            return False
        return format(value, 'f').rstrip('0').rstrip('.') or '0'

    @api.model
    def _get_transcription_price(self, transcript):
        usage = getattr(transcript, 'usage', None)
        duration = getattr(usage, 'seconds', None)
        if not isinstance(duration, (int, float, Decimal)):
            duration = getattr(transcript, 'duration', None)
        if not isinstance(duration, (int, float, Decimal)):
            logger.warning('OpenAI transcription response has no duration')
            return False
        price = (
            Decimal(str(duration))
            * self._TRANSCRIPTION_PRICE_PER_MINUTE
            / Decimal('60')
        )
        return self._format_transcription_price(price)

    def _get_transcription_model(self):
        """Transcription model name; modelnexus_connect returns the configured one."""
        return self._TRANSCRIPTION_MODEL

    def _get_completion_model(self):
        """Summary model name; modelnexus_connect returns the configured one."""
        return (
            os.environ.get('OPENAI_COMPLETION_MODEL')
            or self.env['connect.settings'].get_param(
                'openai_summary_model', 'gpt-5.4-mini')
        )

    def make_summary(self, client, summary_prompt, transcript):
        logger.info('Make summary!')
        try:
            model = self._get_completion_model()
            max_tokens = int(os.environ.get('OPENAI_COMPLETION_MAX_TOKENS', 4096))
            completion_params = {
                'model': model,
                'messages': [
                    {
                        'role': 'user',
                        'content': summary_prompt
                    },
                    {
                        'role': 'user',
                        'content': transcript,
                    },
                ],
            }
            if model.startswith('gpt-5'):
                completion_params['max_completion_tokens'] = max_tokens
            else:
                completion_params.update({
                    'temperature': float(os.environ.get(
                        'OPENAI_COMPLETION_TEMPERATURE', 0.5)),
                    'max_tokens': max_tokens,
                    'top_p': float(os.environ.get('OPENAI_COMPLETION_TOP_P', 1.0)),
                    'frequency_penalty': float(os.environ.get(
                        'OPENAI_COMPLETION_FREQUENCY_PENALTY', 0.0)),
                    'presence_penalty': float(os.environ.get(
                        'OPENAI_COMPLETION_PRESENCE_PENALTY',
                        os.environ.get('OPENAI_COMPLETION_PRESENSE_PENALTY', 0.0))),
                })
            response = client.chat.completions.create(**completion_params)
            logger.info('%s', response.usage)
            return {'summary': response.choices[0].message.content.strip('\n\n')}
        except Exception as e:
            logger.exception(f'Summary error: {e}')
            return {'transcription_error': str(e)}

    @api.model
    def _is_summarizable(self, transcript):
        """True when the transcript has enough words to summarize."""
        words = re.findall(r'\w+', TIMESTAMP_RE.sub(' ', transcript or ''))
        return len(words) >= MIN_SUMMARY_WORDS

    def get_transcript(self, fail_silently=False):
        self.ensure_one()
        openai_key = self.env['connect.settings'].sudo().get_param('openai_api_key')
        if not openai_key:
            if fail_silently:
                logger.warning('OpenAI key is not set! Transcription will not be available.')
                return False
            else:
                raise ValidationError('OpenAI key is not set!')
        summary_prompt = self.env['connect.settings'].get_param('summary_prompt')
        if not self.media_url and not self.recording_attachment:
            raise ValidationError('Recording is not available yet!')
        self.transcribe_recording(openai_key, summary_prompt)

    @api.private
    def update_transcript(self, data):
        self.ensure_one()
        call = self.call
        vals = {
            'transcript': data.get('transcript'),
            'transcription_price': self._format_transcription_price(
                data.get('transcription_price')
            ),
            'summary': (
                data.get('summary')
                if self._is_summarizable(data.get('transcript')) else False),
            'transcription_token': False,
            'transcription_error': data.get('transcription_error'),
            'transcription_pending': False,
        }
        self.with_context(tracking_disable=True).write(vals)
        self._delete_after_successful_transcription()
        if call:
            self.env['connect.settings'].connect_reload_view('connect.call')
        self.env['connect.settings'].connect_reload_view('connect.recording')
        if data.get('notify_uid'):
            self.env['connect.settings'].connect_notify(
                'Transcript updated', notify_uid=data['notify_uid'])

    def _delete_after_successful_transcription(self):
        """Remove processed audio only after its analysis is durable."""
        self.ensure_one()
        if (
            not self.exists()
            or not self.call
            or not self.transcript
            or self.transcription_error
        ):
            return False
        delete_recording = self.env['connect.settings'].sudo().get_param(
            'delete_recording_after_transcription'
        )
        if not delete_recording:
            return False
        self.unlink()
        return True

    def _get_recording_widget(self):
        proxy_recordings = self.env['connect.settings'].sudo().get_param('proxy_recordings')
        for rec in self:
            media_url = rec._get_media_src(proxy_recordings)
            if not media_url:
                rec.recording_widget = ''
                continue
            # media_url may be a raw, webhook-supplied URL when
            # proxy_recordings is off; escape it before it lands in the
            # sanitize=False Html field to prevent stored XSS.
            rec.recording_widget = '<audio id="sound_file" preload="auto" ' \
                'controls="controls"> ' \
                '<source src="{}"/>' \
                '</audio>'.format(escape(media_url))

    def _get_media_src(self, proxy_recordings):
        """Return the URL the player should point at, '' when there is none.

        Seam: storage add-ons (connect_twilio's S3 storage) override this to
        hand out a backend-specific URL, e.g. an S3 presigned URL.
        """
        self.ensure_one()
        if self.recording_attachment:
            return self.get_attachment_media_url()
        if self.media_url:
            if proxy_recordings:
                return '/connect/recording/{}'.format(self.id)
            return self.media_url
        return ''

    def _get_media_download_url(self):
        """Return the provider URL to download this recording from now.

        Called only when the audio is actually fetched (proxy playback and
        transcription), never while rendering a list or form, so a provider
        override may call its API here. Seam: providers whose stored link is
        short-lived (connect_telnyx: 10-minute signed URLs) override this to
        request a fresh link.
        """
        self.ensure_one()
        return self.media_url

    def get_attachment_media_url(self):
        self.ensure_one()
        if not self.recording_attachment:
            return ''
        filename = quote(self.recording_filename or 'recording.wav')
        return (
            '/web/content?model=connect.recording'
            '&id={}&field=recording_attachment'
            '&filename_field=recording_filename'
            '&filename={}&download=True'.format(self.id, filename))

    def _get_list_view_summary(self):
        for rec in self:
            rec.list_view_summary = rec.summary

    def unlink(self):
        self._sync_analysis_to_call()
        return super().unlink()

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get('skip_transcription'):
            recs = super().create(vals_list)
        else:
            transcript_calls = self.env['connect.settings'].sudo().get_param('transcript_calls')
            recs = super(Recording, self.with_context(
                mail_create_nosubscribe=True, mail_create_nolog=True)).create(vals_list)
            if transcript_calls:
                # Defer transcription to _cron_transcribe_recordings. Running
                # the Whisper + GPT round trip inline blocked the provider
                # webhook that created the recording (timeout -> retry ->
                # duplicate processing) and was only survivable via a
                # mid-create cr.commit() that broke the caller transaction's
                # atomicity. The flag is the cron's work queue instead.
                recs.filtered(lambda r: not r.transcript).transcription_pending = True
        recs._redact_stored_audio()
        recs._queue_provider_delete()
        return recs

    @api.model
    def _cron_transcribe_recordings(self, limit=20):
        """Transcribe recordings queued by create() (transcription_pending).

        Runs out of the request path so a slow OpenAI round trip never
        blocks a provider webhook. Each recording is committed
        independently so one failure does not roll back the batch.
        """
        pending = self.search([('transcription_pending', '=', True)], limit=limit)
        for rec in pending:
            try:
                if not rec.transcript:
                    rec.get_transcript(fail_silently=True)
            except Exception:
                logger.exception('Cron transcript error for recording %s', rec.id)
            # Clear the flag whether or not it succeeded: transcribe_recording
            # records the transcript or the error, and a single attempt
            # matches the previous inline behaviour while avoiding an
            # unbounded retry loop. The commit is safe here — the cron owns
            # its own transaction, unlike create().
            if rec.exists():
                rec.transcription_pending = False
            self.env.cr.commit()

    @api.depends('duration')
    def _get_duration_human(self):
        for record in self:
            if record.duration is not None:
                minutes = record.duration // 60
                seconds = record.duration % 60
                record.duration_human = '{:02}:{:02}'.format(minutes, seconds)
            else:
                record.duration_human = "00:00"

    @api.constrains('call', 'transcript', 'summary')
    def _sync_analysis_to_call(self):
        for rec in self.filtered('call'):
            recordings = self.search(
                [('call', '=', rec.call.id)], order='id desc'
            )
            vals = {}
            latest_transcript = recordings.filtered('transcript')[:1]
            latest_summary = recordings.filtered('summary')[:1]
            if rec == latest_transcript:
                vals['transcript'] = rec.transcript
            if rec == latest_summary:
                vals['summary'] = rec.summary
            if vals:
                rec.call.with_user(SUPERUSER_ID).write(vals)
