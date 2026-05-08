# -*- coding: utf-8 -*-
"""1.2.0: drop the legacy S3 migration cron.

The every-minute `S3 Migration: Move recordings to object storage` cron is
gone — migration now runs synchronously from the wizard, one batch per click.
Existing installs carry a stale cron record (the data file was loaded with
noupdate=1), so unlink it here.
"""
import logging

from odoo import api, SUPERUSER_ID

logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    cron = env.ref('connect_s3.ir_cron_s3_migration', raise_if_not_found=False)
    if cron:
        cron.unlink()
        logger.info('Removed legacy S3 migration cron')
