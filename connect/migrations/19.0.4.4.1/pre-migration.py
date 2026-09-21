"""Retarget ir_model_data.model for Twilio records the 19.0.4.4.0 cutover missed.

19.0.4.4.0 re-owned xmlids connect.domain_route_call → connect_twilio.domain_route_call
but left ir_model_data.model as connect.twiml. Installing connect_twilio then
fails: xmlid connect_twilio.domain_route_call found record of different model
connect.twiml. Idempotent — a 0-row UPDATE is the already-fixed case.
"""

import logging

_logger = logging.getLogger(__name__)

MOVE_MODELS = (
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


def migrate(cr, version):
    if not version:
        return
    for old, new in MOVE_MODELS:
        cr.execute(
            "UPDATE ir_model_data SET model = %s WHERE model = %s",
            (new, old),
        )
        if cr.rowcount:
            _logger.info(
                'retargeted %s ir_model_data rows %s -> %s',
                cr.rowcount, old, new,
            )
