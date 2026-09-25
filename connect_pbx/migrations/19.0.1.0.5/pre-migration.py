"""Give connect_pbx ownership of the business-hours schedule xmlid.

connect_pbx defines `schedule_us_business_hours` in its own data file. A database
whose ir_model_data row for that record is still owned by `connect` gets the row
re-owned in place before the data loads, so the existing `connect.schedule` record
is kept and no second one is created.
"""

import logging

_logger = logging.getLogger(__name__)

OLD_MODULE = 'connect'
NEW_MODULE = 'connect_pbx'
NAME = 'schedule_us_business_hours'


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        "SELECT module FROM ir_model_data WHERE module IN %s AND name = %s",
        ((OLD_MODULE, NEW_MODULE), NAME),
    )
    modules = [row[0] for row in cr.fetchall()]
    old_rows = modules.count(OLD_MODULE)
    if not old_rows:
        return
    if old_rows > 1 or NEW_MODULE in modules:
        raise AssertionError(
            'cannot re-own %s.%s: found rows for %s' % (OLD_MODULE, NAME, modules)
        )
    cr.execute(
        "UPDATE ir_model_data SET module = %s WHERE module = %s AND name = %s",
        (NEW_MODULE, OLD_MODULE, NAME),
    )
    if cr.rowcount != 1:
        raise AssertionError('re-owned %s rows, expected 1' % cr.rowcount)
    _logger.info('re-owned ir_model_data %s.%s -> %s', OLD_MODULE, NAME, NEW_MODULE)
