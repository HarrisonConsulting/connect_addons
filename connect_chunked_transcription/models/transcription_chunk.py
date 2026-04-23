import base64
import logging
import time

from odoo import _, fields, models

try:
    from odoo.addons.queue_job.exception import RetryableJobError
except ImportError:
    # queue_job not installed; module guard also applies via __manifest__.
    RetryableJobError = Exception  # type: ignore[assignment,misc]

_logger = logging.getLogger(__name__)

# Upstream error signatures that should force a retry via queue_job.
_TRANSIENT_SIGNS = ('timeout', '408', '429', '502', '503', '504', 'connection', 'temporar')


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
        stitches once all chunks are done."""
        self.ensure_one()
        if self.state == 'cancelled':
            return
        if not self.audio_data:
            self._mark_failed('chunk has no audio data (may have been cleared)')
            return

        self.state = 'in_progress'
        self.attempts = (self.attempts or 0) + 1

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
            if any(sig in str(e).lower() for sig in _TRANSIENT_SIGNS):
                self.error_message = msg
                self.state = 'queued'
                raise RetryableJobError(
                    f'transient transcription error (attempt {self.attempts}): {msg}',
                    seconds=min(60 * self.attempts, 600),
                    ignore_retry=False,
                )
            _logger.exception('chunk %s permanent failure', self.id)
            self._mark_failed(msg)
            return

        duration_ms = int((time.time() - t0) * 1000)

        segments = None
        if segments_raw:
            segments = [self._offset_segment(s) for s in segments_raw]

        self.write({
            'transcript_text': text,
            'transcript_segments': segments,
            'model': model_name,
            'duration_ms': duration_ms,
            'state': 'done',
            'error_message': False,
            'audio_data': False,  # free the PCM now that we have text
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
        self.write({
            'state': 'queued',
            'error_message': False,
        })
        self.with_delay(
            channel='root.connect_transcription',
            description=f'Manual retry chunk {self.sequence} of job {self.job_id.id}',
            identity_key=f'connect.transcription.chunk:{self.id}',
        ).transcribe()
