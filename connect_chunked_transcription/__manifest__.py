{
    'name': 'Connect Chunked Transcription',
    'version': '19.0.1.0.0',
    'category': 'Productivity/Telephony',
    'summary': 'Chunk long recordings at silence boundaries and transcribe reliably via queue_job',
    'description': """
Connect Chunked Transcription
=============================

Production-grade transcription path for long recordings (5 min to multi-hour):

- Pure-Python audio splitting via `miniaudio` + RMS silence detection — no
  ffmpeg required (odoo.sh compatible).
- Each chunk transcribed as a separate `queue_job` job with automatic retry,
  so transient LiteLLM / whisper timeouts don't discard work.
- Chunks stitched back into a single transcript with preserved timestamps.
- Short recordings (<= configurable threshold) still go through the base
  synchronous path — no overhead for brief calls.

Drop-in override of `connect.recording.transcribe_recording`. Zero changes
to the base `connect` module.
""",
    'author': 'Harrison Consulting, LLC',
    'website': 'https://www.harrison.consulting',
    'depends': [
        'connect',
        'queue_job',
    ],
    'external_dependencies': {
        'python': ['miniaudio', 'numpy'],
    },
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/ir_config_parameter.xml',
        'data/queue_job_channel.xml',
        'data/ir_cron.xml',
        'views/transcription_job_views.xml',
        'views/transcription_chunk_views.xml',
        'views/recording_views.xml',
        'views/menus.xml',
    ],
    'license': 'OPL-1',
    'installable': True,
    'application': False,
    'auto_install': False,
}
