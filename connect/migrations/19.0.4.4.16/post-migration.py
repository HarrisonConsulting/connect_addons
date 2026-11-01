"""Point stored model names at the Twilio models they now live under.

The provider split renamed ten connect.* models to connect.twilio.* in place.
Chatter messages, followers, attachments and TwiML model.method targets keep
the model name as a string, so rows written before the split still name a
model the registry no longer has. Only exact old names are rewritten.
"""
import logging

from odoo.tools import SQL

_logger = logging.getLogger(__name__)

MOVED_MODELS = (
    ('connect.exten', 'connect.twilio.exten'),
    ('connect.callflow', 'connect.twilio.callflow'),
    ('connect.callflow_choice', 'connect.twilio.callflow_choice'),
    ('connect.number', 'connect.twilio.number'),
    ('connect.outgoing_callerid', 'connect.twilio.outgoing_callerid'),
    ('connect.user_callflow', 'connect.twilio.user_callflow'),
    ('connect.user_callflow_call', 'connect.twilio.user_callflow_call'),
    ('connect.message_configuration', 'connect.twilio.message_configuration'),
    ('connect.domain', 'connect.twilio.domain'),
    ('connect.twiml', 'connect.twilio.twiml'),
)

MODEL_NAME_COLUMNS = (
    ('mail_message', 'model'),
    ('mail_followers', 'res_model'),
    ('ir_attachment', 'res_model'),
    ('connect_twilio_twiml', 'model'),
)


def _column_exists(cr, table, column):
    cr.execute(
        """
        SELECT 1 FROM information_schema.columns
         WHERE table_name = %s AND column_name = %s
        """,
        (table, column),
    )
    return bool(cr.fetchone())


def rename_model_references(cr, moved_models=MOVED_MODELS):
    for table, column in MODEL_NAME_COLUMNS:
        if not _column_exists(cr, table, column):
            continue
        for old, new in moved_models:
            # Precedent: /mnt/19/odoo/odoo/addons/base/models/ir_model.py
            cr.execute(SQL(
                'UPDATE %s SET %s = %s WHERE %s = %s',
                SQL.identifier(table), SQL.identifier(column), new,
                SQL.identifier(column), old,
            ))
            if cr.rowcount:
                _logger.info(
                    'retargeted %s %s.%s rows %s -> %s',
                    cr.rowcount, table, column, old, new,
                )


def migrate(cr, version):
    if not version:
        return
    rename_model_references(cr)
