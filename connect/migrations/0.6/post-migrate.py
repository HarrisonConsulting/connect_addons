import logging
from odoo import api
from odoo.api import SUPERUSER_ID
from odoo.tools.sql import rename_column

logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    # Sync numbers
    if not env['connect.number'].search([]):
        logger.info('No DID numbers to migrate.')
        return
    # Sync outgoing calleid numbers
    env['connect.outgoing_callerid'].sync()
    # Copy numbers
    for user in env['connect.user'].search([]):
        if user.callerid_number:
            callerid = env['connect.outgoing_callerid'].search([
                ('number', '=', user.callerid_number.phone_number)])
            if callerid:
                user.outgoing_callerid = callerid
                logger.info('CallerId %s for user %s migrated.', callerid.number, user.name)
