# -*- coding: utf-8 -*-
"""Pre-migrate for 1.16.1.

Drops columns superseded by the connect.audio library refactor:

  * connect_callflow.voice — the legacy per-callflow Char holding a Twilio
    voice name (e.g. 'man'/'woman'/'Polly.Joanna'). Subsumed by
    connect.audio.voice_id in 1.14.0: _pin_callflow_voices walked every
    callflow at that time and copied this value onto each migrated audio's
    voice_id, so no runtime code still reads it. Dropping the column here
    prevents ORM 'unknown field' log spam on upgrade and aligns the schema
    with the model definition (field removed from connect/models/callflow.py
    in this release).

Runs in pre-migrate because the field no longer exists in the model — we
want the column gone before the ORM scans the callflow model and tries to
reconcile schema.
"""

import logging

logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'connect_callflow' AND column_name = 'voice'
    """)
    if cr.fetchone():
        cr.execute('ALTER TABLE connect_callflow DROP COLUMN voice')
        logger.info('Dropped connect_callflow.voice '
                    '(superseded by connect.audio.voice_id).')
