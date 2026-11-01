# -*- coding: utf-8 -*-
"""
Migration script to convert call statuses to new business-meaningful values.

Old (Twilio-based): completed, no-answer, busy, failed, canceled
New (Business-meaningful): answered, voicemail, missed, busy, rejected, failed

Status mapping:
- completed + child completed → answered (someone picked up)
- completed + voicemail_url → voicemail (caller left a message)
- completed + no child completed + no voicemail → missed (early hangup)
- no-answer → missed
- busy → busy
- failed → failed
- canceled → missed

Priority when multiple children exist:
1. answered - if ANY child completed
2. voicemail - if voicemail_url is set
3. rejected - if ANY child rejected
4. busy - if ANY child busy
5. missed - default for no-answer/canceled
6. failed - if ALL children failed
"""
import logging
from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Convert call statuses to new business-meaningful values."""
    env = api.Environment(cr, SUPERUSER_ID, {})

    # === STEP 1: Convert 'completed' calls to 'answered' where a child was completed ===
    cr.execute("""
        SELECT DISTINCT c.id
        FROM connect_call c
        JOIN connect_channel ch ON ch.call = c.id
        WHERE c.status = 'completed'
          AND ch.status = 'completed'
          AND ch.parent_channel IS NOT NULL
    """)
    answered_ids = [row[0] for row in cr.fetchall()]

    if answered_ids:
        _logger.info(f'Converting {len(answered_ids)} calls from "completed" to "answered"')
        for call_id in answered_ids:
            call = env['connect.call'].browse(call_id)
            completed_child = next(
                (ch for ch in call.channels if ch.status == 'completed' and ch.parent_channel),
                None
            )
            vals = {'status': 'answered'}
            if completed_child and completed_child.called_pbx_user:
                vals['answered_pbx_user'] = completed_child.called_pbx_user.id
                if completed_child.called_pbx_user.user:
                    vals['answered_user'] = completed_child.called_pbx_user.user.id
            call.write(vals)

    # === STEP 2: Convert calls with voicemail to 'voicemail' status ===
    cr.execute("""
        SELECT id FROM connect_call
        WHERE voicemail_url IS NOT NULL
          AND voicemail_url != ''
          AND status NOT IN ('answered', 'voicemail')
    """)
    voicemail_ids = [row[0] for row in cr.fetchall()]

    if voicemail_ids:
        _logger.info(f'Converting {len(voicemail_ids)} calls to "voicemail" status')
        env['connect.call'].browse(voicemail_ids).write({'status': 'voicemail'})

    # === STEP 3: Convert 'no-answer' to 'missed' ===
    cr.execute("""
        SELECT id FROM connect_call
        WHERE status = 'no-answer'
    """)
    no_answer_ids = [row[0] for row in cr.fetchall()]

    if no_answer_ids:
        _logger.info(f'Converting {len(no_answer_ids)} calls from "no-answer" to "missed"')
        env['connect.call'].browse(no_answer_ids).write({'status': 'missed'})

    # === STEP 4: Convert 'canceled' to 'missed' ===
    cr.execute("""
        SELECT id FROM connect_call
        WHERE status = 'canceled'
    """)
    canceled_ids = [row[0] for row in cr.fetchall()]

    if canceled_ids:
        _logger.info(f'Converting {len(canceled_ids)} calls from "canceled" to "missed"')
        env['connect.call'].browse(canceled_ids).write({'status': 'missed'})

    # === STEP 5: Handle remaining 'completed' calls (no child completed, no voicemail) ===
    # These were likely early hangups - convert to 'missed'
    cr.execute("""
        SELECT c.id
        FROM connect_call c
        WHERE c.status = 'completed'
          AND c.direction = 'incoming'
          AND (c.voicemail_url IS NULL OR c.voicemail_url = '')
          AND NOT EXISTS (
              SELECT 1 FROM connect_channel ch
              WHERE ch.call = c.id
                AND ch.status = 'completed'
                AND ch.parent_channel IS NOT NULL
          )
    """)
    early_hangup_ids = [row[0] for row in cr.fetchall()]

    if early_hangup_ids:
        _logger.info(f'Converting {len(early_hangup_ids)} early-hangup calls to appropriate status')
        for call_id in early_hangup_ids:
            call = env['connect.call'].browse(call_id)
            child_channels = [ch for ch in call.channels if ch.parent_channel]
            if child_channels:
                # Use priority-based resolution
                child_statuses = [ch.status for ch in child_channels]
                if 'rejected' in child_statuses:
                    call.write({'status': 'rejected'})
                elif 'busy' in child_statuses:
                    call.write({'status': 'busy'})
                elif all(s == 'failed' for s in child_statuses):
                    call.write({'status': 'failed'})
                else:
                    call.write({'status': 'missed'})
            else:
                call.write({'status': 'missed'})

    # === STEP 6: Convert remaining 'completed' outgoing calls to 'answered' ===
    cr.execute("""
        SELECT id FROM connect_call
        WHERE status = 'completed'
          AND direction = 'outgoing'
    """)
    outgoing_completed_ids = [row[0] for row in cr.fetchall()]

    if outgoing_completed_ids:
        _logger.info(f'Converting {len(outgoing_completed_ids)} outgoing "completed" calls to "answered"')
        env['connect.call'].browse(outgoing_completed_ids).write({'status': 'answered'})

    total = (len(answered_ids) + len(voicemail_ids) + len(no_answer_ids) +
             len(canceled_ids) + len(early_hangup_ids) + len(outgoing_completed_ids))
    _logger.info(f'Connect migration 1.0.25: Converted {total} call statuses to new values')
