import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ConnectTranscriptionJob(models.Model):
    """Parent record for a chunked transcription. Coordinates N chunk jobs
    and stitches their results back into the originating recording."""
    _name = 'connect.transcription.job'
    _description = 'Chunked Transcription Job'
    _order = 'create_date desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    recording_id = fields.Many2one(
        'connect.recording', required=True, ondelete='cascade', index=True,
    )
    summary_prompt = fields.Text(
        help='Captured from the original transcribe_recording call so the '
             'stitch step can invoke summarisation identically.',
    )

    state = fields.Selection(
        [('pending', 'Pending'),
         ('chunking', 'Chunking'),
         ('transcribing', 'Transcribing'),
         ('stitching', 'Stitching'),
         ('done', 'Done'),
         ('failed', 'Failed'),
         ('cancelled', 'Cancelled')],
        default='pending', required=True, tracking=True, index=True,
    )

    target_chunk_seconds = fields.Integer(default=180, required=True)
    min_silence_ms = fields.Integer(default=500, required=True)
    silence_rms_db = fields.Float(default=-40.0, required=True)

    chunk_ids = fields.One2many('connect.transcription.chunk', 'job_id')
    total_chunks = fields.Integer(compute='_compute_chunk_stats', store=True)
    completed_chunks = fields.Integer(compute='_compute_chunk_stats', store=True)
    failed_chunks = fields.Integer(compute='_compute_chunk_stats', store=True)
    progress_percent = fields.Float(compute='_compute_chunk_stats', store=True)

    audio_duration_seconds = fields.Float(
        digits=(16, 3),
        help='Total audio duration in seconds (sum of chunk durations).',
    )
    error_message = fields.Text(readonly=True)

    stitched_transcript = fields.Text(readonly=True)

    @api.depends('chunk_ids', 'chunk_ids.state')
    def _compute_chunk_stats(self):
        for job in self:
            chunks = job.chunk_ids
            total = len(chunks)
            done = len(chunks.filtered(lambda c: c.state == 'done'))
            failed = len(chunks.filtered(lambda c: c.state == 'failed'))
            job.total_chunks = total
            job.completed_chunks = done
            job.failed_chunks = failed
            job.progress_percent = (100.0 * done / total) if total else 0.0

    def _notify_chunk_done(self):
        """Called by a chunk on terminal state. Uses a row-level lock to
        prevent concurrent stitches when multiple chunks finish at the same
        time (N queue_job workers racing to the finish line).
        """
        self.ensure_one()
        # SELECT ... FOR UPDATE serialises _notify_chunk_done callers.
        # Without this, N workers concurrently observe all(done) and call
        # _stitch_and_finalize N times — double summary billing + duplicate
        # writes to the recording.
        self.env.cr.execute(
            'SELECT state FROM connect_transcription_job WHERE id = %s FOR UPDATE',
            (self.id,),
        )
        row = self.env.cr.fetchone()
        if not row:
            return
        current_state = row[0]
        if current_state not in ('transcribing',):
            # Already stitching / done / failed / cancelled — another worker
            # owns the terminal transition.
            return

        chunks = self.chunk_ids
        if not chunks:
            return
        terminal = chunks.filtered(lambda c: c.state in ('done', 'failed', 'cancelled'))
        if len(terminal) < len(chunks):
            return  # still chunks in flight
        # Transition out of 'transcribing' inside the lock; a concurrent call
        # from another worker will see the new state and return above.
        if any(c.state == 'failed' for c in chunks):
            self._mark_failed(_('One or more chunks failed after retries; '
                                'see chunk records for details.'))
            return
        self.state = 'stitching'
        self.env.cr.commit()  # release the row lock before slow summary call
        self._stitch_and_finalize()

    def _stitch_and_finalize(self):
        """Concatenate chunks in sequence order into the recording's transcript
        and trigger summarisation via the recording's existing pipeline."""
        self.ensure_one()
        self.state = 'stitching'
        chunks = self.chunk_ids.sorted('sequence')

        # Render the transcript in the same timestamped format the base
        # connect._call_transcription_api emits ("HH:MM:SS text\n"), so the
        # summary path and UI display keep working identically.
        lines = []
        for c in chunks:
            segs = c.transcript_segments or []
            if segs:
                for s in segs:
                    start = int(s.get('start') or 0)
                    ts = (f"{start // 3600:02d}:"
                          f"{(start % 3600) // 60:02d}:"
                          f"{start % 60:02d}")
                    text = (s.get('text') or '').strip()
                    if text:
                        lines.append(f'{ts} {text}')
            else:
                text = (c.transcript_text or '').strip()
                if text:
                    offset = int(c.start_offset_seconds or 0)
                    ts = (f"{offset // 3600:02d}:"
                          f"{(offset % 3600) // 60:02d}:"
                          f"{offset % 60:02d}")
                    lines.append(f'{ts} {text}')
        combined = '\n'.join(lines).strip()
        self.stitched_transcript = combined

        # Generate summary using the recording's existing helper, same as the
        # base synchronous path. make_summary returns {'summary': ...} on
        # success or {'transcription_error': ...} on failure — never
        # transcription_price. Don't read a key it doesn't produce.
        summary_result = {}
        recording = self.recording_id
        try:
            client = self.env['connect.settings'].get_openai_client()
            summary_result = recording.make_summary(
                client, self.summary_prompt or '', combined,
            ) or {}
        except Exception as e:
            _logger.exception('transcription_job %s: summary failed', self.id)
            summary_result = {'transcription_error': f'Summary failed: {e}'}

        update_vals = {
            'transcript': combined,
            'summary': summary_result.get('summary'),
            'transcription_error': summary_result.get('transcription_error', False),
        }
        try:
            recording.update_transcript(update_vals)
        except Exception as e:
            _logger.exception('transcription_job %s: failed to update recording', self.id)
            self._mark_failed(str(e))
            return

        # Free chunk PCM only after the recording update committed — keeps
        # chunks re-stitchable if we ever need a manual recovery.
        chunks.write({'audio_data': False})

        self.state = 'done'
        self.message_post(
            body=_('Stitched transcript into recording (%(n)d chunks, %(d).1fs audio).',
                   n=len(chunks), d=self.audio_duration_seconds or 0.0),
        )

    def _mark_failed(self, message: str):
        self.ensure_one()
        self.state = 'failed'
        self.error_message = message or 'unknown error'
        try:
            self.recording_id.update_transcript({
                'transcription_error': message or 'chunked transcription failed',
            })
        except Exception:
            _logger.exception('transcription_job %s: failed to write error to recording', self.id)
        self.message_post(body=_('Job failed: %s', message or 'unknown error'))

    def action_retry_failed(self):
        """Re-enqueue all failed chunks."""
        self.ensure_one()
        failed = self.chunk_ids.filtered(lambda c: c.state == 'failed')
        if not failed:
            raise UserError(_('No failed chunks to retry.'))
        for chunk in failed:
            chunk.state = 'queued'
            chunk.attempts = 0
            chunk.error_message = False
            chunk.with_delay(
                channel='root.connect_transcription',
                description=f'Retry transcription chunk {chunk.sequence} of job {self.id}',
            ).transcribe()
        if self.state == 'failed':
            self.state = 'transcribing'
            self.error_message = False
        return True

    def action_cancel(self):
        self.ensure_one()
        if self.state in ('done', 'cancelled'):
            return
        self.state = 'cancelled'
        self.chunk_ids.filtered(lambda c: c.state in ('pending', 'queued')).write(
            {'state': 'cancelled'}
        )
        self.recording_id.update_transcript({
            'transcription_error': _('Transcription cancelled'),
        })

    def action_view_chunks(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Chunks — Job #%s', self.id),
            'res_model': 'connect.transcription.chunk',
            'view_mode': 'list,form',
            'domain': [('job_id', '=', self.id)],
            'context': {'default_job_id': self.id},
        }
