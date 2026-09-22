from . import models
from . import controllers
from . import wizard

import logging
from odoo import fields

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    try:
        module = env['ir.module.module'].search([('name', '=', 'connect')], limit=1)
        if module:
            module.write({'create_date': fields.Datetime.now()})
        env['oduist.license'].update_license_status(raise_exc=False)
        _alias_legacy_xmlids(env)
    except Exception as e:
        _logger.error('Error in post_init_hook: %s', str(e))


def _alias_legacy_xmlids(env):
    """Point HC xmlids at the NG records so connect_pbx / elevenlabs menus resolve."""
    aliases = (
        ('group_admin', 'group_connect_admin'),
        ('group_user', 'group_connect_user'),
        ('group_webhook', 'group_connect_webhook'),
        ('module_category_connect', 'module_connect_category'),
        ('menu_connect_root', 'connect_top_menu'),
        ('menu_connect_settings', 'connect_settings_menu'),
    )
    cr = env.cr
    for canonical, alias in aliases:
        cr.execute(
            """
            SELECT model, res_id, noupdate
              FROM ir_model_data
             WHERE module = 'connect' AND name = %s
            """,
            (canonical,),
        )
        row = cr.fetchone()
        if not row:
            continue
        cr.execute(
            """
            INSERT INTO ir_model_data
                (module, name, model, res_id, noupdate,
                 create_uid, create_date, write_uid, write_date)
            SELECT 'connect', %s, %s, %s, true, 1, NOW(), 1, NOW()
             WHERE NOT EXISTS (
                   SELECT 1 FROM ir_model_data
                    WHERE module = 'connect' AND name = %s
             )
            """,
            (alias, row[0], row[1], alias),
        )
        cr.execute(
            """
            UPDATE ir_model_data
               SET noupdate = true
             WHERE module = 'connect' AND name = %s
            """,
            (alias,),
        )
