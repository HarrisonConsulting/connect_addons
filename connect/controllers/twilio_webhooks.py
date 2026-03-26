# -*- coding: utf-8 -*

import logging

from odoo.http import request, Controller, route, Response
from twilio.request_validator import RequestValidator

logger = logging.getLogger(__name__)


class ConnectController(Controller):

    @staticmethod
    def _reject_invalid_request():
        return Response('<Response><Hangup/></Response>', status=403, content_type='text/xml')

    @staticmethod
    def check_signature(data, region=True):
        if not request.env['connect.settings'].sudo().get_param('twilio_verify_requests'):
            logger.warning('SECURITY: Twilio webhook signature verification is DISABLED')
            return True
        settings = request.env['connect.settings'].sudo()
        if region:
            auth_token = settings.get_param('region_auth_token') or settings.get_param('auth_token')
        else:
            auth_token = settings.get_param('auth_token')
        validator = RequestValidator(auth_token)
        url = request.httprequest.url.replace('http:', 'https:')
        signature = request.httprequest.headers.get('X-Twilio-Signature', '')
        request_valid = validator.validate(url, data, signature)
        if not request_valid:
            if request.httprequest.url.startswith('http:'):
                logger.error('Twilio requires HTTPS to be setup!')
            else:
                logger.error('Twilio request is not valid!')
        return request_valid

    @route('/twilio/webhook/domain', methods=['POST'], type='http', auth='public', csrf=False)
    def domain_webhook(self, **kw):
        if not self.check_signature(kw):
            return self._reject_invalid_request()
        try:
            domain = request.env['connect.domain'].with_user(request.env.ref("connect.user_connect_webhook"))
            res = domain.route_call(kw)
            return f'{res}'
        except Exception:
            logger.exception('domain_webhook failed for CallSid=%s', kw.get('CallSid'))
            return '<Response><Say>A system error occurred. Please try again later.</Say><Hangup/></Response>'

    @route('/twilio/webhook/callstatus', methods=['POST'], type='http', auth='public', csrf=False)
    def callstatus_webhook(self, **kw):
        if not self.check_signature(kw):
            return self._reject_invalid_request()
        try:
            res = request.env['connect.call'].with_user(
                request.env.ref("connect.user_connect_webhook")
            ).on_call_status(kw)
            return f'{res}'
        except Exception:
            logger.exception('callstatus_webhook failed for CallSid=%s', kw.get('CallSid'))
            return Response("Internal error", status=500)

    @route('/twilio/webhook/number', methods=['POST'], type='http', auth='public', csrf=False)
    def number_webhook(self, **kw):
        if not self.check_signature(kw):
            return self._reject_invalid_request()
        try:
            res = request.env['connect.number'].with_user(request.env.ref("connect.user_connect_webhook")).route_call(kw)
            return f'{res}'
        except Exception:
            logger.exception('number_webhook failed for CallSid=%s', kw.get('CallSid'))
            return '<Response><Say>A system error occurred. Please try again later.</Say><Hangup/></Response>'

    @route('/twilio/webhook/outgoing_callerid', methods=['POST'], type='http', auth='public', csrf=False)
    def outgoing_callerid_webhook(self, **kw):
        if not self.check_signature(kw):
            return self._reject_invalid_request()
        try:
            env = request.env
            outgoing_callerid = env['connect.outgoing_callerid'].with_user(env.ref("connect.user_connect_webhook"))
            res = outgoing_callerid.update_status(kw)
            return f'{res}'
        except Exception:
            logger.exception('outgoing_callerid_webhook failed')
            return Response("Internal error", status=500)

    @route('/twilio/webhook/callflow/<int:flow_id>/gather', methods=['POST'], type='http', auth='public', csrf=False)
    def gather_webhook(self, flow_id, **kw):
        if not self.check_signature(kw):
            return self._reject_invalid_request()
        try:
            callflow = request.env['connect.callflow'].with_user(request.env.ref("connect.user_connect_webhook"))
            res = callflow.gather_action(flow_id, kw)
            return f'{res}'
        except Exception:
            logger.exception('gather_webhook failed for flow_id=%s CallSid=%s', flow_id, kw.get('CallSid'))
            return '<Response><Say>A system error occurred. Please try again later.</Say><Hangup/></Response>'

    @route('/twilio/webhook/vm_recordingstatus', methods=['POST'], type='http', auth='public', csrf=False)
    def vm_recording_status_webhook(self, **kw):
        if not self.check_signature(kw):
            return self._reject_invalid_request()
        try:
            call = request.env['connect.call'].with_user(request.env.ref("connect.user_connect_webhook"))
            res = call.on_vm_recording_status(kw)
            return f'{res}'
        except Exception:
            logger.exception('vm_recording_status_webhook failed for CallSid=%s', kw.get('CallSid'))
            return Response("Internal error", status=500)

    @route('/twilio/webhook/<string:model_name>/call_action/<int:record_id>', methods=['POST'], type='http', auth='public', csrf=False)
    def call_action_edit_webhook(self, model_name, record_id, **kw):
        if not self.check_signature(kw):
            return self._reject_invalid_request()
        try:
            model = request.env[model_name].with_user(request.env.ref("connect.user_connect_webhook"))
            res = model.on_call_action(record_id, kw)
            return f'{res}'
        except Exception:
            logger.exception('call_action_edit_webhook failed for %s/%s CallSid=%s', model_name, record_id, kw.get('CallSid'))
            return '<Response><Say>A system error occurred. Please try again later.</Say><Hangup/></Response>'

    @route('/twilio/webhook/recordingstatus', methods=['POST'], type='http', auth='public', csrf=False)
    def recording_status_webhook(self, **kw):
        if not self.check_signature(kw):
            return self._reject_invalid_request()
        try:
            recording = request.env['connect.recording'].with_user(request.env.ref("connect.user_connect_webhook"))
            res = recording.on_recording_status(kw)
            return f'{res}'
        except Exception:
            logger.exception('recording_status_webhook failed for RecordingSid=%s', kw.get('RecordingSid'))
            return Response("Internal error", status=500)

    @route('/twilio/webhook/callaction', methods=['POST'], type='http', auth='public', csrf=False)
    def call_action_webhook(self, **kw):
        if not self.check_signature(kw):
            return self._reject_invalid_request()
        try:
            call = request.env['connect.call'].with_user(request.env.ref("connect.user_connect_webhook"))
            res = call.on_call_action(kw)
            return f'{res}'
        except Exception:
            logger.exception('call_action_webhook failed for CallSid=%s', kw.get('CallSid'))
            return '<Response><Say>A system error occurred. Please try again later.</Say><Hangup/></Response>'

    @route('/twilio/webhook/twiml/<int:twiml_id>', methods=['POST'], type='http', auth='public', csrf=False)
    def twiml_webhook(self, twiml_id, **kw):
        if not self.check_signature(kw):
            return self._reject_invalid_request()
        try:
            twiml = request.env['connect.twiml'].with_user(request.env.ref("connect.user_connect_webhook"))
            res = twiml.browse(twiml_id).render(kw)
            return f'{res}'
        except Exception:
            logger.exception('twiml_webhook failed for twiml_id=%s CallSid=%s', twiml_id, kw.get('CallSid'))
            return '<Response><Say>A system error occurred. Please try again later.</Say><Hangup/></Response>'

    @route('/twilio/webhook/message', methods=['POST'], type='http', auth='public', csrf=False)
    def message_webhook(self, **kw):
        if not self.check_signature(kw, region=False):
            return self._reject_invalid_request()
        try:
            message = request.env['connect.message'].with_user(request.env.ref("connect.user_connect_webhook"))
            res = message.receive(kw)
            return f'{res}'
        except Exception:
            logger.exception('message_webhook failed for MessageSid=%s', kw.get('MessageSid'))
            return Response("Internal error", status=500)

    @route('/twilio/webhook/message_status', methods=['POST'], type='http', auth='public', csrf=False)
    def message_status_webhook(self, **kw):
        if not self.check_signature(kw, region=False):
            return self._reject_invalid_request()
        try:
            request.env['connect.message'].with_user(request.env.ref("connect.user_connect_webhook")).update_message_status(kw)
            return 'OK'
        except Exception:
            logger.exception('message_status_webhook failed for MessageSid=%s', kw.get('MessageSid'))
            return Response("Internal error", status=500)

    @route('/twilio/webhook/whatsapp_message_status', methods=['POST'], type='http', auth='public', csrf=False)
    def whatsapp_message_status_webhook(self, **kw):
        if not self.check_signature(kw, region=False):
            return self._reject_invalid_request()
        try:
            request.env['connect.whatsapp_sender'].with_user(request.env.ref("connect.user_connect_webhook")).update_message_status(kw)
            return 'OK'
        except Exception:
            logger.exception('whatsapp_message_status_webhook failed for MessageSid=%s', kw.get('MessageSid'))
            return Response("Internal error", status=500)

    @route('/twilio/webhook/transfer_continuation', methods=['POST'], type='http', auth='public', csrf=False)
    def transfer_continuation_webhook(self, **kw):
        if not self.check_signature(kw):
            return self._reject_invalid_request()
        try:
            transfer_wizard = request.env['connect.transfer_wizard'].with_user(request.env.ref("connect.user_connect_webhook"))
            res = transfer_wizard.handle_transfer_continuation(kw)
            return f'{res}'
        except Exception:
            logger.exception('transfer_continuation_webhook failed for CallSid=%s', kw.get('CallSid'))
            return '<Response><Say>A system error occurred. Please try again later.</Say><Hangup/></Response>'

    @route('/twilio/webhook/conference_event', methods=['POST'], type='http', auth='public', csrf=False)
    def conference_event_webhook(self, **kw):
        if not self.check_signature(kw):
            return self._reject_invalid_request()
        try:
            res = request.env['connect.call'].with_user(
                request.env.ref("connect.user_connect_webhook")
            ).on_conference_event(kw)
            return f'{res}'
        except Exception:
            logger.exception('conference_event_webhook failed for ConferenceSid=%s', kw.get('ConferenceSid'))
            return Response("Internal error", status=500)
