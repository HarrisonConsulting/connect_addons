from odoo import models
from odoo.exceptions import ValidationError


class Settings(models.Model):
    _inherit = "connect.settings"

    def sync(self):
        super().sync()
        self.env["connect.byoc"].sync()

    def get_external_call_route(self, number, callerId, status_url,
            record='do-not-record', record_status_url=None):
        # External call to PSTN. Find outgoing rule.
        rule = self.env["connect.outgoing_rule"].sudo().find_rule(number)
        if not rule:
            raise ValidationError("No outgoing rule found for this destination!")
        if not rule.byoc:
            return super().get_external_call_route(number, callerId, status_url)
        rule = rule.sudo()
        callerId = self.env['connect.twilio.domain'].get_byoc_caller_id_number(rule.byoc, number, callerId)
        call_duration_limit = int(self.sudo().get_param('call_duration_limit'))
        record_attr = ' recordingStatusCallback="{}"'.format(record_status_url) if record_status_url else ''
        twiml = """
        <Response>
            <Dial record="{}"{} callerId="{}" timeLimit="{}"><Number byoc="{}" statusCallback='{}' statusCallbackEvent='initiated answered completed'>{}</Number></Dial>
        </Response>
        """.format(
            record,
            record_attr,
            callerId,
            call_duration_limit,
            rule.byoc.sid,
            status_url,
            rule._byoc_dial_number(number),
        )
        return twiml
