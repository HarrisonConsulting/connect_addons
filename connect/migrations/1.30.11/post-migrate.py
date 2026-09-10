import ast

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Remove the obsolete usage job and its scheduled-action concealment."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    beacon = env.ref('connect.update_usage', raise_if_not_found=False)
    notification = env.ref('mail.ir_cron_module_update_notification', raise_if_not_found=False)
    action = env.ref('base.ir_cron_act', raise_if_not_found=False)
    if action and action.domain:
        try:
            domain = ast.literal_eval(action.domain)
        except (ValueError, SyntaxError):
            domain = None
        hidden_ids = {record.id for record in (beacon, notification) if record}
        if (
            isinstance(domain, list) and len(domain) == 1
            and isinstance(domain[0], (tuple, list)) and len(domain[0]) == 3
            and domain[0][:2] in (('id', 'not in'), ['id', 'not in'])
            and isinstance(domain[0][2], (list, tuple))
            and all(isinstance(value, int) for value in domain[0][2])
            and set(domain[0][2]) == hidden_ids
        ):
            action.write({'domain': False})
    if beacon:
        beacon.unlink()
    template = env.ref('connect.module_version_template', raise_if_not_found=False)
    if template:
        template.unlink()
