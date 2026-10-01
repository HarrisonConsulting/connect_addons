"""Keep the regional auth token columns when Connect stops defining them.

The two fields move onto connect_twilio under the same names. Re-own their
ir.model.data rows before Connect's cleanup so the columns and stored tokens
stay on connect_settings.
"""

import logging

_logger = logging.getLogger(__name__)

OLD_MODULE = 'connect'
NEW_MODULE = 'connect_twilio'
FIELD_MODEL = 'ir.model.fields'
NAMES = (
    'field_connect_settings__region_auth_token',
    'field_connect_settings__display_region_auth_token',
)


def migrate(cr, version):
    if not version:
        return
    for name in NAMES:
        cr.execute(
            """
            SELECT module
              FROM ir_model_data
             WHERE module IN %s
               AND name = %s
               AND model = %s
            """,
            ((OLD_MODULE, NEW_MODULE), name, FIELD_MODEL),
        )
        modules = [row[0] for row in cr.fetchall()]
        old_rows = modules.count(OLD_MODULE)
        if not old_rows:
            continue
        if old_rows > 1 or NEW_MODULE in modules:
            raise AssertionError(
                'cannot re-own %s.%s: found rows for %s' % (OLD_MODULE, name, modules)
            )
        cr.execute(
            """
            UPDATE ir_model_data
               SET module = %s
             WHERE module = %s
               AND name = %s
               AND model = %s
            """,
            (NEW_MODULE, OLD_MODULE, name, FIELD_MODEL),
        )
        if cr.rowcount != 1:
            raise AssertionError(
                're-owned %s rows for %s, expected 1' % (cr.rowcount, name)
            )
        _logger.info(
            're-owned ir_model_data %s.%s -> %s', OLD_MODULE, name, NEW_MODULE
        )
