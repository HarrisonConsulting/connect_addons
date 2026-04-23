import base64
import logging
import os

from odoo import _, api, fields, models

from ..utils.audio_chunker import chunk_audio_at_silences

_logger = logging.getLogger(__name__)


class ConnectRecording(models.Model):
    _inherit = 'connect.recording'

    chunked_job_id = fields.Many2one(
        'connect.transcription.job', string='Chunked Job',
        readonly=True, copy=False,
    )
    is_chunked_transcription = fields.Boolean(
        compute='_compute_is_chunked_transcription', store=False,
    )

    def _compute_is_chunked_transcription(self):
        for rec in self:
            rec.is_chunked_transcription = bool(rec.chunked_job_id)

    def _get_chunked_threshold_bytes(self):
        """Bytes above which we switch from single-shot to chunked transcription."""
        raw = self.env['ir.config_parameter'].sudo().get_param(
            'connect_chunked_transcription.threshold_mb', default='10'
        )
        try:
            mb = int(raw)
        except (TypeError, ValueError):
            mb = 10
        return max(1, mb) * 1024 * 1024

    def _get_chunk_params(self):
        P = self.env['ir.config_parameter'].sudo()
        def _f(key, default):
            try:
                return float(P.get_param(key, default=default))
            except (TypeError, ValueError):
                return float(default)
        def _i(key, default):
            try:
                return int(P.get_param(key, default=default))
            except (TypeError, ValueError):
                return int(default)
        return {
            'target_chunk_seconds': _f('connect_chunked_transcription.target_chunk_seconds', 180),
            'min_silence_duration_ms': _i('connect_chunked_transcription.min_silence_ms', 500),
            'silence_rms_db': _f('connect_chunked_transcription.silence_rms_db', -40.0),
        }

    def transcribe_recording(self, openai_api_key, summary_prompt):
        """Override: split long audio at silences and dispatch per-chunk queue_job.

        Short recordings continue through the base (synchronous) path so brief
        calls are transcribed immediately without queue overhead.
        """
        self.ensure_one()

        # If a chunked job already exists and is active, don't re-enter.
        if self.chunked_job_id and self.chunked_job_id.state in (
            'pending', 'chunking', 'transcribing', 'stitching',
        ):
            _logger.info('recording %s: chunked job %s already active — skipping',
                         self.id, self.chunked_job_id.id)
            return

        temp_path = None
        try:
            temp_path = self._download_recording_audio()
            if not temp_path:
                self.write({'transcription_error': 'Recording media not available'})
                return

            size_bytes = os.path.getsize(temp_path)
            threshold = self._get_chunked_threshold_bytes()
            if size_bytes <= threshold:
                # Delegate to the base synchronous path — nothing changes for
                # short recordings.
                return super().transcribe_recording(openai_api_key, summary_prompt)

            # Long-audio path: chunk + enqueue.
            with open(temp_path, 'rb') as fh:
                audio_bytes = fh.read()
            return self._dispatch_chunked_transcription(
                audio_bytes=audio_bytes,
                filename=os.path.basename(temp_path),
                summary_prompt=summary_prompt,
            )
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    _logger.warning('could not remove temp file %s', temp_path)

    def _dispatch_chunked_transcription(self, audio_bytes, filename, summary_prompt):
        """Create a job + chunk records and enqueue per-chunk transcription."""
        self.ensure_one()
        params = self._get_chunk_params()

        try:
            chunks_data = chunk_audio_at_silences(
                input_bytes=audio_bytes,
                input_filename=filename,
                **params,
            )
        except ValueError as e:
            # Unreadable audio — decode failed. Don't enqueue anything.
            _logger.error('recording %s: audio decode failed: %s', self.id, e)
            self.write({'transcription_error': f'Audio decode failed: {e}'})
            return
        except Exception as e:
            _logger.exception('recording %s: unexpected chunking error', self.id)
            self.write({'transcription_error': f'Chunking failed: {e}'})
            return

        if not chunks_data:
            self.write({'transcription_error': 'Audio file produced zero chunks (empty?)'})
            return

        # Sum chunk durations — avoids a second full decode pass and matches
        # exactly what the chunker emitted (so the job's reported duration is
        # consistent with the billable audio).
        total_duration = sum(c['duration_seconds'] for c in chunks_data)

        job = self.env['connect.transcription.job'].create({
            'recording_id': self.id,
            'summary_prompt': summary_prompt,
            'state': 'transcribing',
            'target_chunk_seconds': int(params['target_chunk_seconds']),
            'min_silence_ms': int(params['min_silence_duration_ms']),
            'silence_rms_db': params['silence_rms_db'],
            'audio_duration_seconds': total_duration,
        })

        chunks = self.env['connect.transcription.chunk']
        for idx, cd in enumerate(chunks_data, start=1):
            chunk = self.env['connect.transcription.chunk'].create({
                'job_id': job.id,
                'sequence': idx,
                'filename': cd['filename'],
                'start_offset_seconds': cd['start_offset_seconds'],
                'duration_seconds': cd['duration_seconds'],
                'audio_data': base64.b64encode(cd['bytes']),
                'state': 'queued',
            })
            chunks |= chunk
            chunk.with_delay(
                channel='root.connect_transcription',
                description=f'Transcribe chunk {idx}/{len(chunks_data)} of recording {self.id}',
                identity_key=f'connect.transcription.chunk:{chunk.id}',
            ).transcribe()

        self.with_context(tracking_disable=True).write({
            'chunked_job_id': job.id,
            'transcript': _(
                'Transcription in progress — %(n)s chunks queued (%(d).1fs of audio). '
                'Transcript will populate when all chunks complete.',
                n=len(chunks_data), d=job.audio_duration_seconds,
            ),
            'transcription_error': False,
        })

        _logger.info(
            'recording %s: dispatched chunked transcription job=%s chunks=%d duration=%.1fs',
            self.id, job.id, len(chunks_data), job.audio_duration_seconds,
        )
        return job

    # Called by transcription_chunk.transcribe — keeps API wiring on the
    # recording where the existing OpenAI client resolution lives.
    def _transcribe_chunk_bytes(self, audio_bytes, filename, response_format='verbose_json'):
        """Transcribe a single chunk. Returns (text, segments, model_name).

        Raises on any failure; caller (chunk.transcribe) decides retry vs fail.
        """
        self.ensure_one()
        import io
        client = self.env['connect.settings'].get_openai_client()
        model = self._get_transcription_model_chunked()

        file_tuple = (
            filename or 'chunk.wav',
            io.BytesIO(audio_bytes),
            'audio/wav',
        )
        resp = client.audio.transcriptions.create(
            model=model,
            file=file_tuple,
            response_format=response_format,
            timestamp_granularities=['segment'],
        )
        if response_format == 'verbose_json':
            text = getattr(resp, 'text', '') or ''
            segments = getattr(resp, 'segments', None) or []
            return text, segments, model
        return (str(resp) if resp is not None else ''), None, model

    def _get_transcription_model_chunked(self):
        """Separate override hook — chunked path typically wants the self-hosted
        route (orion/whisper-large-v3) while short calls may fall through to
        whatever _get_transcription_model returns."""
        return self.env['ir.config_parameter'].sudo().get_param(
            'connect_chunked_transcription.model',
            default='orion/whisper-large-v3',
        )
