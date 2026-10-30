import hashlib
import hmac
import json
import logging
import re

from odoo.http import Controller, request, route

_logger = logging.getLogger(__name__)

MAX_RELAY_BYTES = 16384
RELAY_PATH = re.compile(r'(/textable/webhook/relay/)\S+')
_REDACTOR_MARKER = 'connect_textable_relay_redactor'


class RelayPathRedactor(logging.Filter):
    """Drop the relay path token from werkzeug's access log lines.

    The request line sits in the record's args (with ANSI colour codes on
    a terminal), so the message is rendered first and stored back without
    args.
    """
    marker = _REDACTOR_MARKER

    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never break logging
            return True
        if '/textable/webhook/relay/' in message:
            record.msg = RELAY_PATH.sub(r'\1[redacted]', message)
            record.args = ()
        return True


def install_relay_log_redaction():
    werkzeug_logger = logging.getLogger('werkzeug')
    if not any(getattr(f, 'marker', None) == _REDACTOR_MARKER
               for f in werkzeug_logger.filters):
        werkzeug_logger.addFilter(RelayPathRedactor())


install_relay_log_redaction()


def _reply(status):
    return request.make_json_response(
        {'status': 'ok' if status == 200 else 'error'}, status=status)


class TextableRelayController(Controller):
    """Textable relay webhook.

    Textable signs nothing (spec 5.29.16, x-webhooks.relayWebhook), so the
    path token is the only secret: its sha256 is compared in constant time
    and every missing precondition fails closed. The payload must then name
    the configured organization and a configured Textable user, and the
    owned side of the message must be a number that user holds here.
    """

    @route('/textable/webhook/relay/<string:token>', type='http', auth='public',
           methods=['POST'], csrf=False, save_session=False,
           max_content_length=MAX_RELAY_BYTES)
    def textable_relay(self, token, **kwargs):
        httprequest = request.httprequest
        length = httprequest.content_length
        if length is None:
            return _reply(411)
        if length > MAX_RELAY_BYTES:
            return _reply(413)
        expected = request.env['connect.settings'].sudo()._textable_relay_token_hash()
        presented = hashlib.sha256(token.encode()).hexdigest()
        if not expected:
            _logger.critical(
                'SECURITY: Textable relay post refused: no relay secret is configured.')
            return _reply(404)
        if not hmac.compare_digest(presented, expected):
            _logger.critical(
                'SECURITY: Textable relay post refused: the relay token does not match.')
            return _reply(404)
        if httprequest.mimetype != 'application/json':
            return _reply(415)
        try:
            payload = json.loads(httprequest.get_data(cache=False, parse_form_data=False))
        except ValueError:
            return _reply(400)
        webhook_user = request.env.ref('connect.user_connect_webhook')
        outcome = request.env['connect.message'].with_user(webhook_user)._textable_relay(payload)
        if outcome == 'refused':
            return _reply(404)
        if outcome == 'invalid':
            return _reply(400)
        return _reply(200)
