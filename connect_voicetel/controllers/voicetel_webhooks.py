# -*- coding: utf-8 -*-
import logging
from urllib.parse import urlsplit

from odoo.http import request, Controller, route
from voiceml.webhooks import SIGNATURE_HEADER, validate_request

logger = logging.getLogger(__name__)

INVALID = '<Response><Say>Invalid VoiceTel request!</Say></Response>'


def _webhook_env(model):
    return request.env[model].with_user(
        request.env.ref("connect.user_connect_webhook"))


def signed_url(url):
    """The URL as VoiceML signed it: always https.

    Only the scheme is rewritten, so an ``http:`` inside the query string
    (a callback URL passed as a parameter) is left alone.
    """
    return urlsplit(url)._replace(scheme='https').geturl()


class ConnectVoiceTelController(Controller):
    """VoiceML webhooks.

    VoiceML signs every callback and every VoiceML fetch with
    X-Twilio-Signature keyed with the account api_key, over the URL as
    transmitted plus the POST form (ADR-066 decision 8). These routes are
    auth='public', so the signature is the only gate: every missing
    precondition rejects the request.
    """

    @staticmethod
    def check_signature():
        settings = request.env['connect.settings'].sudo()
        if not settings.get_param('voicetel_verify_requests'):
            logger.critical(
                'SECURITY: VoiceTel webhook signature verification is '
                'disabled; rejecting the request.')
            return False
        api_key = settings.get_param('voicetel_api_key')
        if not api_key:
            logger.critical(
                'SECURITY: VoiceTel webhook signature verification has no '
                'API key; rejecting the request.')
            return False
        signature = request.httprequest.headers.get(SIGNATURE_HEADER, '')
        if not signature:
            logger.error('VoiceTel request carries no signature.')
            return False
        # The signature covers the request URL -- query string included --
        # plus the POST body parameters, every value of a repeated key.
        # Odoo merges the query string into the route kwargs, so validating
        # with those would count every query parameter twice.
        params = (
            request.httprequest.form
            if request.httprequest.method == 'POST'
            else None
        )
        request_valid = validate_request(
            api_key, signed_url(request.httprequest.url), params, signature)
        if not request_valid:
            if request.httprequest.url.startswith('http:'):
                logger.error('VoiceTel request is not valid; is HTTPS set up?')
            else:
                logger.error('VoiceTel request is not valid!')
        return request_valid

    @route('/voicetel/webhook/domain', methods=['POST'], type='http', auth='public', csrf=False)
    def domain_webhook(self, **kw):
        if not self.check_signature():
            return INVALID
        res = _webhook_env('connect.voicetel.domain').route_call(kw)
        return f'{res}'

    @route('/voicetel/webhook/callstatus', methods=['POST'], type='http', auth='public', csrf=False)
    def callstatus_webhook(self, **kw):
        if not self.check_signature():
            return ''
        res = _webhook_env('connect.call').on_voicetel_call_status(kw)
        return f'{res}'

    @route('/voicetel/webhook/number', methods=['POST'], type='http', auth='public', csrf=False)
    def number_webhook(self, **kw):
        if not self.check_signature():
            return INVALID
        res = _webhook_env('connect.voicetel.number').route_call(kw)
        return f'{res}'

    @route('/voicetel/webhook/outgoing_callerid', methods=['POST'], type='http', auth='public', csrf=False)
    def outgoing_callerid_webhook(self, **kw):
        if not self.check_signature():
            return ''
        res = _webhook_env('connect.voicetel.outgoing_callerid').update_status(kw)
        return f'{res}'

    @route('/voicetel/webhook/callflow/<int:flow_id>/gather', methods=['POST'], type='http', auth='public', csrf=False)
    def gather_webhook(self, flow_id, **kw):
        if not self.check_signature():
            return INVALID
        res = _webhook_env('connect.voicetel.callflow').gather_action(flow_id, kw)
        return f'{res}'

    @route('/voicetel/webhook/vm_recordingstatus', methods=['POST'], type='http', auth='public', csrf=False)
    def vm_recording_status_webhook(self, **kw):
        if not self.check_signature():
            return INVALID
        res = _webhook_env('connect.call').on_voicetel_vm_recording_status(kw)
        return f'{res}'

    @route('/voicetel/webhook/<string:model_name>/call_action/<int:record_id>', methods=['POST'], type='http', auth='public', csrf=False)
    def call_action_edit_webhook(self, model_name, record_id, **kw):
        if not self.check_signature():
            return INVALID
        if model_name not in ('connect.user', 'connect.voicetel.callflow'):
            return INVALID
        res = _webhook_env(model_name).voicetel_on_call_action(record_id, kw)
        return f'{res}'

    @route('/voicetel/webhook/recordingstatus', methods=['POST'], type='http', auth='public', csrf=False)
    def recording_status_webhook(self, **kw):
        if not self.check_signature():
            return ''
        res = _webhook_env('connect.recording').on_voicetel_recording_status(kw)
        return f'{res}'

    @route('/voicetel/webhook/callaction', methods=['POST'], type='http', auth='public', csrf=False)
    def call_action_webhook(self, **kw):
        if not self.check_signature():
            return INVALID
        res = _webhook_env('connect.call').on_voicetel_call_action(kw)
        return f'{res}'

    @route('/voicetel/webhook/voiceml/<int:voiceml_id>', methods=['POST'], type='http', auth='public', csrf=False)
    def voiceml_webhook(self, voiceml_id, **kw):
        if not self.check_signature():
            return INVALID
        res = _webhook_env('connect.voicetel.voiceml').browse(voiceml_id).render(kw)
        return f'{res}'

    @route('/voicetel/webhook/message', methods=['POST'], type='http', auth='public', csrf=False)
    def message_webhook(self, **kw):
        if not self.check_signature():
            return INVALID
        res = _webhook_env('connect.message').voicetel_receive(kw)
        return f'{res}'
