"""Delete recordings from the carrier after archiving, on existing databases too.

Recordings must not be kept at any carrier. A fresh install gets the setting
on by its default; this turns it on where Connect is already installed and
retires the Twilio-only setting it replaces. A neutralized copy (an odoo.sh
staging or dev build) gets it off: a copy must never delete a production
carrier's recordings.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    icp = env['ir.config_parameter'].sudo()
    enabled = not icp.get_param('database.is_neutralized')
    env['connect.settings'].sudo().search([]).with_context(skip_icp_sync=True).write({
        'delete_provider_recording': enabled,
    })
    icp.set_param('connect.delete_provider_recording', 'True' if enabled else 'False')
    icp.search([('key', '=', 'connect.delete_twilio_recording')]).unlink()
    cr.execute('ALTER TABLE connect_settings DROP COLUMN IF EXISTS delete_twilio_recording')
    _logger.info('Carrier delete after archiving is %s', 'on' if enabled else 'off (neutralized copy)')
