import logging

logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        UPDATE connect_call c
        SET voicemail_stage_id = (
            SELECT id FROM connect_voicemail_stage ORDER BY sequence ASC, id ASC LIMIT 1
        )
        WHERE c.voicemail_url IS NOT NULL
          AND c.voicemail_stage_id IS NULL
    """)
    logger.info("Backfilled voicemail_stage_id for %d existing voicemail records", cr.rowcount)
