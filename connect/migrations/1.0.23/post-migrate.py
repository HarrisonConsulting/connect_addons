# -*- coding: utf-8 -*-
"""
Migration script to fix stuck call price records.

This migration marks calls that will never have a Twilio price as "fetched"
to prevent the cron job from endlessly retrying them:

1. Zero-duration calls (never connected) - Twilio doesn't charge for these
2. Calls stuck in 'initiated' status - never completed
3. Old calls (>7 days) that still have no price - give up retrying

See: https://www.twilio.com/docs/voice/api/call-resource
Twilio note: "price" is "Populated after the call is completed. May not be
immediately available."
"""
import logging
from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Fix stuck call price records that will never have prices."""
    env = api.Environment(cr, SUPERUSER_ID, {})

    # 1. Mark zero-duration calls as fetched (they will never have a price)
    # These are calls that never connected - no charge from Twilio
    zero_duration_calls = env['connect.call'].search([
        ('is_price_fetched', '=', False),
        ('call_sid', '!=', False),
        ('duration', '=', 0),
    ])
    if zero_duration_calls:
        zero_duration_calls.write({
            'is_price_fetched': True,
            'price': 0.0,
        })
        _logger.info(
            f'Marked {len(zero_duration_calls)} zero-duration calls as price_fetched=True'
        )

    # 2. Mark calls stuck in 'initiated' status as fetched
    # These calls never completed and won't have prices
    initiated_calls = env['connect.call'].search([
        ('is_price_fetched', '=', False),
        ('call_sid', '!=', False),
        ('status', '=', 'initiated'),
    ])
    if initiated_calls:
        initiated_calls.write({
            'is_price_fetched': True,
            'price': 0.0,
        })
        _logger.info(
            f'Marked {len(initiated_calls)} initiated-status calls as price_fetched=True'
        )

    # 3. Mark old calls (>7 days) without prices as fetched
    # If Twilio hasn't provided a price by now, it never will
    from datetime import timedelta
    from odoo import fields

    cutoff_date = fields.Datetime.now() - timedelta(days=7)
    old_calls = env['connect.call'].search([
        ('is_price_fetched', '=', False),
        ('call_sid', '!=', False),
        ('create_date', '<', cutoff_date),
    ])
    if old_calls:
        old_calls.write({
            'is_price_fetched': True,
            # Keep existing price (might be 0.0 or a partial value)
        })
        _logger.info(
            f'Marked {len(old_calls)} old calls (>7 days) as price_fetched=True'
        )

    total_fixed = len(zero_duration_calls) + len(initiated_calls) + len(old_calls)
    _logger.info(f'Connect migration 1.0.23: Fixed {total_fixed} stuck call price records')
