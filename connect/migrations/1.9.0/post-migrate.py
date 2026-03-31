# -*- coding: utf-8 -*-
"""Backfill connect.conversation records from existing connect.message data."""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # 1. Create the conversation records by grouping messages by phone pair + channel
    _logger.info('Backfilling connect.conversation records from existing messages...')

    # Determine channel from message_type: 'WhatsApp' -> 'whatsapp', else 'sms'
    cr.execute("""
        INSERT INTO connect_conversation (
            channel_type, phone_a, phone_b, conversation_key,
            active, create_uid, write_uid, create_date, write_date
        )
        SELECT
            CASE WHEN message_type = 'WhatsApp' THEN 'whatsapp' ELSE 'sms' END AS channel_type,
            LEAST(from_number, to_number) AS phone_a,
            GREATEST(from_number, to_number) AS phone_b,
            CASE WHEN message_type = 'WhatsApp' THEN 'whatsapp' ELSE 'sms' END
                || ':' || LEAST(from_number, to_number) || '|' || GREATEST(from_number, to_number)
                AS conversation_key,
            true,
            1, 1, NOW(), NOW()
        FROM connect_message
        WHERE from_number IS NOT NULL AND to_number IS NOT NULL
        GROUP BY
            CASE WHEN message_type = 'WhatsApp' THEN 'whatsapp' ELSE 'sms' END,
            LEAST(from_number, to_number),
            GREATEST(from_number, to_number)
        ON CONFLICT DO NOTHING
    """)
    created = cr.rowcount
    _logger.info('Created %d conversation records', created)

    # 2. Link messages to their conversations
    cr.execute("""
        UPDATE connect_message m
        SET conversation_id = c.id
        FROM connect_conversation c
        WHERE c.conversation_key = (
            CASE WHEN m.message_type = 'WhatsApp' THEN 'whatsapp' ELSE 'sms' END
            || ':' || LEAST(m.from_number, m.to_number) || '|' || GREATEST(m.from_number, m.to_number)
        )
        AND m.conversation_id IS NULL
        AND m.from_number IS NOT NULL
        AND m.to_number IS NOT NULL
    """)
    linked = cr.rowcount
    _logger.info('Linked %d messages to conversations', linked)

    # 3. Set partner_id on conversations from the most frequent partner in messages
    cr.execute("""
        UPDATE connect_conversation c
        SET partner_id = sub.partner
        FROM (
            SELECT DISTINCT ON (conversation_id) conversation_id, partner
            FROM connect_message
            WHERE conversation_id IS NOT NULL AND partner IS NOT NULL
            GROUP BY conversation_id, partner
            ORDER BY conversation_id, COUNT(*) DESC
        ) sub
        WHERE c.id = sub.conversation_id
        AND c.partner_id IS NULL
    """)
    partners = cr.rowcount
    _logger.info('Set partner on %d conversations', partners)

    # 4. Recompute last_message fields using SQL for performance
    cr.execute("""
        UPDATE connect_conversation c
        SET
            last_message_date = sub.last_date,
            last_message_body = LEFT(sub.last_body, 200),
            message_count = sub.msg_count
        FROM (
            SELECT
                m.conversation_id,
                MAX(m.create_date) AS last_date,
                COUNT(*) AS msg_count,
                (ARRAY_AGG(m.body ORDER BY m.create_date DESC))[1] AS last_body
            FROM connect_message m
            WHERE m.conversation_id IS NOT NULL
            GROUP BY m.conversation_id
        ) sub
        WHERE c.id = sub.conversation_id
    """)
    updated = cr.rowcount
    _logger.info('Updated last_message fields on %d conversations', updated)

    # 5. Compute the name field
    cr.execute("""
        UPDATE connect_conversation c
        SET name = COALESCE(p.name, c.phone_b, 'Conversation')
        FROM (SELECT id, partner_id, phone_b FROM connect_conversation) conv
        LEFT JOIN res_partner p ON p.id = conv.partner_id
        WHERE c.id = conv.id
    """)

    _logger.info('Conversation backfill complete')
