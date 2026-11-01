"""Delete the retry-finalization cron whose method no longer exists."""
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    cron = env.ref('connect.ir_cron_retry_stuck_finalizations', raise_if_not_found=False)
    if not cron:
        return
    # Do not delete an operator-repurposed action with the same external ID.
    if (cron.model_id.model != 'connect.call'
            or cron.code.strip() != 'model._retry_stuck_finalizations()'):
        return
    if hasattr(env['connect.call'], '_retry_stuck_finalizations'):
        return
    action = cron.ir_actions_server_id
    cron.unlink()
    action.exists().unlink()
