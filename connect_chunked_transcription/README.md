# connect_chunked_transcription

Production-grade long-audio transcription for `connect.recording`: pure-Python
silence-aware chunking, per-chunk `queue_job` retries, and transcript stitching
that preserves global timestamps.

## Why

`connect.recording.transcribe_recording()` as shipped:

- Caps input at 26 MB and refuses larger files.
- Calls the transcription API synchronously from the create hook — any timeout
  or transient error loses the entire transcription and the user has to
  re-trigger manually.
- Single-shot request against whatever model the proxy points at; LiteLLM's
  default 60 s request_timeout cuts off multi-minute audio.

This module replaces that path for audio above a configurable threshold
(default 10 MB) with a chunk-and-queue flow.

## How

1. Override of `connect.recording.transcribe_recording`. If the downloaded
   audio is larger than `connect_chunked_transcription.threshold_mb`:
2. Decode with `miniaudio` to int16 mono 16 kHz PCM (Whisper-native).
3. RMS silence detection (`-40 dBFS`, `≥500 ms` by default) identifies cut
   candidates within `±30 s` of each target chunk boundary.
4. Pick the longest silence in the window; midpoint is the cut. Hard-cut at
   target if no silence is found (logged at WARNING).
5. Create a `connect.transcription.job` + N `connect.transcription.chunk`
   records. Each chunk carries WAV bytes + absolute start offset.
6. Each chunk is enqueued as a `queue_job` on channel `root.connect_transcription`.
7. Chunk job POSTs to the configured transcription endpoint (default
   `orion/whisper-large-v3` via `ai.harrison.consulting`) with
   `response_format=verbose_json`.
8. Transient failures (timeout, 408, 429, 5xx, connection errors) raise
   `RetryableJobError` with exponential backoff. Permanent failures mark the
   chunk `failed` without retries.
9. When every chunk reaches a terminal state, the job stitches transcripts in
   sequence order, offsets segment timestamps into global time, and updates
   the recording's `transcript` + `summary` fields using the same
   `update_transcript` path the base module uses.

Short recordings (≤ threshold) continue through the unmodified synchronous
path — no queue overhead for brief calls.

## Configuration

`Settings → Technical → System Parameters`:

| Key | Default | Meaning |
|---|---|---|
| `connect_chunked_transcription.threshold_mb` | `10` | Chunk when audio exceeds this size |
| `connect_chunked_transcription.target_chunk_seconds` | `180` | Target seconds per chunk |
| `connect_chunked_transcription.min_silence_ms` | `500` | Min silence duration to be a cut candidate |
| `connect_chunked_transcription.silence_rms_db` | `-40.0` | dBFS threshold for silence |
| `connect_chunked_transcription.model` | `orion/whisper-large-v3` | Route/model name passed to the proxy |

`queue_job` channel: `root.connect_transcription`. Configure concurrency via
`Queue Job → Channels` or the `ODOO_QUEUE_JOB_CHANNELS` env var.

## Dependencies

- `connect` (base telephony + recording)
- `queue_job` (retry / channel / worker pool)
- Python: `miniaudio >= 1.61`, `numpy >= 1.26`

## Memory

Chunker decodes to int16 mono 16 kHz — a 30-minute recording is ~57 MB in
memory during chunking. For 2+ hour recordings consider streaming decode
(miniaudio supports it) before shipping at scale.

## Models

- `connect.transcription.job` — parent coordinator; state machine
  `pending → chunking → transcribing → stitching → done|failed|cancelled`.
- `connect.transcription.chunk` — per-chunk work unit; states
  `pending → queued → in_progress → done|failed|cancelled`. Audio payload is
  cleared when state becomes `done`.

Both models inherit `mail.thread` (job only) for activity logging.

## Operator runbook

**Chunk failing repeatedly** → open the chunk form, read `error_message`. If
transient (API side), the chunk will self-retry. If permanent, investigate
the specific chunk's WAV payload; `Retry` button re-enqueues.

**Job stuck at `transcribing`** → check `Queue Jobs → Failed` for the chunks.
Common causes: bearer token rotated, upstream whisper pod not Ready,
NetworkPolicy drop.

**Manual cancel** → Job form → `Cancel`. Pending/queued chunks are marked
cancelled; already-running chunks complete but are not stitched.
