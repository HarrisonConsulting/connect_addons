# -*- coding: utf-8 -*-
"""Backfill connect.conversation records from existing connect.message data."""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    _logger.info('Backfilling connect.conversation records from existing messages...')

    # Collect all "our" numbers from connect_number and connect_whatsapp_sender
    cr.execute("""
        SELECT phone_number FROM connect_number WHERE phone_number IS NOT NULL
        UNION
        SELECT number FROM connect_whatsapp_sender WHERE number IS NOT NULL
    """)
    our_numbers = {row[0] for row in cr.fetchall()}
    _logger.info('Found %d organization numbers for role resolution', len(our_numbers))

    # 1. Get distinct phone pairs with channel
    cr.execute("""
        SELECT
            CASE WHEN message_type = 'WhatsApp' THEN 'whatsapp' ELSE 'sms' END AS channel,
            from_number, to_number
        FROM connect_message
        WHERE from_number IS NOT NULL AND to_number IS NOT NULL
        GROUP BY channel, from_number, to_number
    """)
    pairs = cr.fetchall()

    # Deduplicate: group by sorted key, resolve phone_a (ours) vs phone_b (theirs)
    seen_keys = {}
    for channel, from_num, to_num in pairs:
        phones_sorted = sorted([from_num, to_num])
        key = f"{channel}:{phones_sorted[0]}|{phones_sorted[1]}"
        if key in seen_keys:
            continue

        # Determine which is "ours"
        if from_num in our_numbers:
            phone_a, phone_b = from_num, to_num
        elif to_num in our_numbers:
            phone_a, phone_b = to_num, from_num
        else:
            # Fallback: use sorted order
            phone_a, phone_b = phones_sorted

        seen_keys[key] = (channel, phone_a, phone_b, key)

    # 2. Bulk insert conversations
    if seen_keys:
        values = list(seen_keys.values())
        args = []
        placeholders = []
        for channel, phone_a, phone_b, key in values:
            placeholders.append("(%s, %s, %s, %s, true, 1, 1, NOW(), NOW())")
            args.extend([channel, phone_a, phone_b, key])

        cr.execute("""
            INSERT INTO connect_conversation (
                channel_type, phone_a, phone_b, conversation_key,
                active, create_uid, write_uid, create_date, write_date
            ) VALUES """ + ", ".join(placeholders) + """
            ON CONFLICT DO NOTHING
        """, args)
        created = cr.rowcount
        _logger.info('Created %d conversation records', created)

    # 3. Link messages to their conversations
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

    # 4. Set partner_id from most frequent partner in messages
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

    # 5. Recompute last_message fields
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

    # 6. Compute the name field
    cr.execute("""
        UPDATE connect_conversation c
        SET name = COALESCE(p.name, c.phone_b, 'Conversation')
        FROM (SELECT id, partner_id, phone_b FROM connect_conversation) conv
        LEFT JOIN res_partner p ON p.id = conv.partner_id
        WHERE c.id = conv.id
    """)

    _logger.info('Conversation backfill complete')
