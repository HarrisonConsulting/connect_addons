"""Backfill rows that used the typo'd 'outboubd-api' technical_direction.

`technical_direction` is a Char field, so the typo wasn't rejected at
write time — but downstream `== 'outbound-api'` comparisons in
`models/call.py` and `models/channel.py` skipped affected rows. Two
write paths produced these rows: `connect/models/settings.py:931` and
`connect_website/models/settings.py:67`. Both are corrected in this
release; this script repairs prior data.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE connect_channel
        SET technical_direction = 'outbound-api'
        WHERE technical_direction = 'outboubd-api'
    """)
    if cr.rowcount:
        _logger.info(
            "connect_channel: backfilled %d rows from 'outboubd-api' to 'outbound-api'",
            cr.rowcount,
        )
