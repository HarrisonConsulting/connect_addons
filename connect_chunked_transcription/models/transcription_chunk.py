import base64
import logging
import time

from odoo import _, api, fields, models

try:
    from odoo.addons.queue_job.exception import RetryableJobError
except ImportError:
    RetryableJobError = Exception  # type: ignore[assignment,misc]

# Prefer exception-type matching over substring scans. OpenAI SDK (used by
# connect.recording's client) raises these on transient conditions; we fold
# them into queue_job retries. Anything else is permanent.
try:
    from openai import (  # type: ignore[import-not-found]
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        RateLimitError,
    )
    _TRANSIENT_EXC = (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError)
except Exception:  # pragma: no cover — SDK absent in some test envs
    _TRANSIENT_EXC = ()

# HTTP status codes the SDK sometimes wraps in generic Exceptions. If the
# exception exposes .status_code or .code matching these, still retry.
_TRANSIENT_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}

# Maximum retries before we mark the chunk failed. Backoff is linear-with-cap.
_MAX_ATTEMPTS = 6

_logger = logging.getLogger(__name__)


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, _TRANSIENT_EXC):
        return True
    status = getattr(exc, 'status_code', None) or getattr(exc, 'code', None)
    try:
        if int(status) in _TRANSIENT_STATUS_CODES:
            return True
    except (TypeError, ValueError):
        pass
    return False


class ConnectTranscriptionChunk(models.Model):
    _name = 'connect.transcription.chunk'
    _description = 'Transcription Chunk'
    _order = 'job_id, sequence'

    job_id = fields.Many2one(
        'connect.transcription.job', required=True, ondelete='cascade', index=True,
    )
    sequence = fields.Integer(required=True, index=True)

    state = fields.Selection(
        [('pending', 'Pending'),
         ('queued', 'Queued'),
         ('in_progress', 'In Progress'),
         ('done', 'Done'),
         ('failed', 'Failed'),
         ('cancelled', 'Cancelled')],
        default='pending', required=True, index=True,
    )

    # Audio payload — cleared on success to keep DB compact.
    audio_data = fields.Binary(attachment=True)
    filename = fields.Char()

    start_offset_seconds = fields.Float(digits=(16, 3), required=True)
    duration_seconds = fields.Float(digits=(16, 3), required=True)

    transcript_text = fields.Text(readonly=True)
    transcript_segments = fields.Json(readonly=True)
    model = fields.Char(readonly=True)
    response_format = fields.Char(default='verbose_json')

    attempts = fields.Integer(default=0, readonly=True)
    error_message = fields.Text(readonly=True)
    duration_ms = fields.Integer(
        readonly=True, help='Wall-clock time of the transcription API call.',
    )

    _unique_chunk_sequence = models.Constraint(
        'UNIQUE(job_id, sequence)',
        'Chunk sequence must be unique within a job.',
    )

    def transcribe(self):
        """queue_job entry point. Retries transient failures; marks permanent
        failures on the chunk. On success, notifies the parent job which
        stitches once all chunks are done.

        Note on retry-attempt accounting: RetryableJobError rolls back the
        current transaction, so any `self.attempts` increment made before
        the raise would be discarded. We therefore read the attempt count
        from the queue.job record (which the queue framework manages outside
        our transaction) to drive backoff and the cap.
        """
        self.ensure_one()
        if self.state == 'cancelled':
            return
        if not self.audio_data:
            self._mark_failed('chunk has no audio data (job may have been finalized)')
            return

        # Prior attempt count comes from the queue.job record. 0 on first run.
        job_uuid = self.env.context.get('job_uuid')
        prior_attempts = 0
        if job_uuid:
            queue_job = self.env['queue.job'].sudo().search(
                [('uuid', '=', job_uuid)], limit=1,
            )
            if queue_job:
                prior_attempts = int(queue_job.retry or 0)
        current_attempt = prior_attempts + 1

        # Single write — consolidates state + attempts in one UPDATE.
        self.write({'state': 'in_progress', 'attempts': current_attempt})

        audio_bytes = base64.b64decode(self.audio_data)
        recording = self.job_id.recording_id
        t0 = time.time()
        try:
            text, segments_raw, model_name = recording._transcribe_chunk_bytes(
                audio_bytes=audio_bytes,
                filename=self.filename,
                response_format=self.response_format or 'verbose_json',
            )
        except Exception as e:
            msg = f'{type(e).__name__}: {e}'
            if _is_transient(e) and current_attempt < _MAX_ATTEMPTS:
                # state='queued' on retry is cosmetic — the raise rolls back
                # this write anyway. Recorded for consistency of the state
                # machine when viewed from an interactive cursor.
                self.write({'state': 'queued', 'error_message': msg})
                raise RetryableJobError(
                    f'transient transcription error (attempt {current_attempt}/{_MAX_ATTEMPTS}): {msg}',
                    seconds=min(60 * current_attempt, 600),
                    ignore_retry=False,
                )
            if current_attempt >= _MAX_ATTEMPTS:
                msg = f'{msg} (gave up after {current_attempt} attempts)'
            _logger.exception('chunk %s permanent failure', self.id)
            self._mark_failed(msg)
            return

        duration_ms = int((time.time() - t0) * 1000)

        segments = None
        if segments_raw:
            segments = [self._offset_segment(s) for s in segments_raw]

        # Do NOT clear audio_data here — the job may need to re-stitch or
        # manually retry a sibling chunk. The parent job clears audio on all
        # chunks only after _stitch_and_finalize writes successfully.
        self.write({
            'transcript_text': text,
            'transcript_segments': segments,
            'model': model_name,
            'duration_ms': duration_ms,
            'state': 'done',
            'error_message': False,
        })
        self.job_id._notify_chunk_done()

    def _offset_segment(self, segment) -> dict:
        """Shift a Whisper segment by this chunk's absolute start offset so the
        stitched transcript has correct global timing."""
        def _get(obj, key, default=None):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        start = float(_get(segment, 'start', 0.0) or 0.0)
        end = float(_get(segment, 'end', 0.0) or 0.0)
        offset = float(self.start_offset_seconds or 0.0)
        return {
            'id': _get(segment, 'id'),
            'start': start + offset,
            'end': end + offset,
            'text': _get(segment, 'text', ''),
            'avg_logprob': _get(segment, 'avg_logprob'),
            'no_speech_prob': _get(segment, 'no_speech_prob'),
        }

    def _mark_failed(self, message: str):
        self.ensure_one()
        self.state = 'failed'
        self.error_message = message or 'unknown error'
        self.job_id._notify_chunk_done()

    def action_retry(self):
        """Manual retry for a single failed chunk."""
        self.ensure_one()
        if self.state not in ('failed', 'cancelled'):
            return
        if not self.audio_data:
            # Can't retry without the source bytes. The parent job cleared
            # them on the last successful stitch — re-running this single
            # chunk now would fail. Surface that clearly.
            self.error_message = (
                'Cannot retry: audio payload cleared after job finalized. '
                'Re-trigger transcription from the originating recording.'
            )
            return
        self.write({
            'state': 'queued',
            'error_message': False,
        })
        self.with_delay(
            channel='root.connect_transcription',
            description=f'Manual retry chunk {self.sequence} of job {self.job_id.id}',
            identity_key=f'connect.transcription.chunk:{self.id}',
        ).transcribe()

    @api.model
    def _cron_watchdog_stuck_chunks(self, threshold_minutes: int = 20):
        """Reset chunks stuck in 'in_progress' past the threshold so queue_job
        picks them back up. Triggered when a worker crashes mid-transcribe."""
        from datetime import datetime, timedelta
        cutoff = datetime.utcnow() - timedelta(minutes=threshold_minutes)
        stuck = self.sudo().search([
            ('state', '=', 'in_progress'),
            ('write_date', '<', cutoff),
        ])
        for chunk in stuck:
            if not chunk.audio_data:
                chunk._mark_failed(
                    f'stuck in in_progress >{threshold_minutes}m and audio cleared'
                )
                continue
            chunk.write({'state': 'queued', 'error_message': 'reset by watchdog'})
            chunk.with_delay(
                channel='root.connect_transcription',
                description=f'Watchdog re-enqueue chunk {chunk.sequence} of job {chunk.job_id.id}',
                identity_key=f'connect.transcription.chunk:{chunk.id}',
            ).transcribe()
        if stuck:
            _logger.info('watchdog: re-enqueued %d stuck chunks', len(stuck))
        return len(stuck)
