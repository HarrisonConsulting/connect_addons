"""Retire the persisted cron whose implementation was removed by NG."""
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    cron = env.ref('connect.ir_cron_retry_stuck_finalizations', raise_if_not_found=False)
    if not cron or not cron.active:
        return
    # Do not disable an operator-repurposed action with the same external ID.
    if (cron.model_id.model != 'connect.call'
            or cron.code.strip() != 'model._retry_stuck_finalizations()'):
        return
    if hasattr(env['connect.call'], '_retry_stuck_finalizations'):
        return
    cron.with_context(tracking_disable=True).write({'active': False})
