# -*- coding: utf-8 -*-
import logging

from odoo.http import request, Controller, route

logger = logging.getLogger(__name__)


class ConnectVoicetelController(Controller):

    @staticmethod
    def check_signature():
        params = (
            request.httprequest.form.to_dict()
            if request.httprequest.method == 'POST'
            else {}
        )
        return request.env['connect.settings']._validate_voicetel_request(
            request.httprequest, params)

    def _fail(self):
        return '<Response><Say>Invalid VoiceTel request!</Say></Response>'

    @route('/voicetel/webhook/domain', methods=['POST'], type='http', auth='public', csrf=False)
    def domain_webhook(self, **kw):
        if not self.check_signature():
            return self._fail()
        domain = request.env['connect.voicetel.domain'].with_user(request.env.ref('connect.user_connect_webhook'))
        return '{}'.format(domain.route_call(kw))

    @route('/voicetel/webhook/callstatus', methods=['POST'], type='http', auth='public', csrf=False)
    def callstatus_webhook(self, **kw):
        if not self.check_signature():
            return False
        res = request.env['connect.call'].with_user(
            request.env.ref('connect.user_connect_webhook')).on_call_status_voicetel(kw)
        return '{}'.format(res)

    @route('/voicetel/webhook/number', methods=['POST'], type='http', auth='public', csrf=False)
    def number_webhook(self, **kw):
        if not self.check_signature():
            return self._fail()
        res = request.env['connect.voicetel.number'].with_user(
            request.env.ref('connect.user_connect_webhook')).route_call(kw)
        return '{}'.format(res)

    @route('/voicetel/webhook/outgoing_callerid', methods=['POST'], type='http', auth='public', csrf=False)
    def outgoing_callerid_webhook(self, **kw):
        if not self.check_signature():
            return False
        outgoing_callerid = request.env['connect.voicetel.outgoing_callerid'].with_user(
            request.env.ref('connect.user_connect_webhook'))
        return '{}'.format(outgoing_callerid.update_status(kw))

    @route('/voicetel/webhook/callflow/<int:flow_id>/gather', methods=['POST'], type='http', auth='public', csrf=False)
    def gather_webhook(self, flow_id, **kw):
        if not self.check_signature():
            return self._fail()
        callflow = request.env['connect.voicetel.callflow'].with_user(
            request.env.ref('connect.user_connect_webhook'))
        return '{}'.format(callflow.gather_action(flow_id, kw))

    @route('/voicetel/webhook/vm_recordingstatus', methods=['POST'], type='http', auth='public', csrf=False)
    def vm_recording_status_webhook(self, **kw):
        if not self.check_signature():
            return self._fail()
        call = request.env['connect.call'].with_user(request.env.ref('connect.user_connect_webhook'))
        return '{}'.format(call.on_vm_recording_status_voicetel(kw))

    @route('/voicetel/webhook/<string:model_name>/call_action/<int:record_id>', methods=['POST'], type='http', auth='public', csrf=False)
    def call_action_edit_webhook(self, model_name, record_id, **kw):
        if not self.check_signature():
            return self._fail()
        model = request.env[model_name].with_user(request.env.ref('connect.user_connect_webhook'))
        res = model.on_call_action(record_id, kw)
        return '{}'.format(res)

    @route('/voicetel/webhook/recordingstatus', methods=['POST'], type='http', auth='public', csrf=False)
    def recording_status_webhook(self, **kw):
        if not self.check_signature():
            return False
        recording = request.env['connect.recording'].with_user(request.env.ref('connect.user_connect_webhook'))
        return '{}'.format(recording.on_recording_status_voicetel(kw))

    @route('/voicetel/webhook/callaction', methods=['POST'], type='http', auth='public', csrf=False)
    def call_action_webhook(self, **kw):
        if not self.check_signature():
            return self._fail()
        call = request.env['connect.call'].with_user(request.env.ref('connect.user_connect_webhook'))
        return '{}'.format(call.on_call_action_voicetel(kw))

    @route('/voicetel/webhook/application/<int:application_id>', methods=['POST'], type='http', auth='public', csrf=False)
    def application_webhook(self, application_id, **kw):
        if not self.check_signature():
            return self._fail()
        application = request.env['connect.voicetel.application'].with_user(
            request.env.ref('connect.user_connect_webhook'))
        return '{}'.format(application.browse(application_id).render(kw))

    @route('/voicetel/webhook/message', methods=['POST'], type='http', auth='public', csrf=False)
    def message_webhook(self, **kw):
        if not self.check_signature():
            return self._fail()
        message = request.env['connect.message'].with_user(request.env.ref('connect.user_connect_webhook'))
        return '{}'.format(message.receive_voicetel(kw))

    @route('/voicetel/webhook/message_status', methods=['POST'], type='http', auth='public', csrf=False)
    def message_status_webhook(self, **kw):
        if not self.check_signature():
            return False
        request.env['connect.message'].with_user(
            request.env.ref('connect.user_connect_webhook')).receive_voicetel(kw)
        return 'OK'
