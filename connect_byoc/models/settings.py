from odoo import models, api
from odoo.exceptions import ValidationError
from ..migrators import BYOCMigrator


class Settings(models.Model):
    _inherit = "connect.settings"

    @api.model
    def _get_account_migrators(self):
        # After DomainMigrator: trunks bind from_domain_sid and the domain's
        # credential list, which exist on the target only once domains migrated.
        return super()._get_account_migrators() + [BYOCMigrator]

    def sync(self):
        super().sync()
        self.env["connect.byoc"].sync()

    def get_external_call_route(self, number, callerId, status_url,
            record='do-not-record', record_status_url=None):
        # External call to PSTN. Find outgoing rule.
        rule = self.env["connect.outgoing_rule"].find_rule(number)
        if not rule:
            raise ValidationError("No outgoing rule found for this destination!")
        callerId = self.env['connect.domain'].get_byoc_caller_id_number(rule.byoc, number, callerId)
        twiml = """
        <Response>
            <Dial record="{}" recordingStatusCallback="{}" callerId="{}"><Number {} statusCallback='{}' statusCallbackEvent='initiated answered completed'>{}</Number></Dial>
        </Response>
        """.format(
            record,
            record_status_url,
            callerId,
            'byoc="{}"'.format(rule.byoc.sid) if rule.byoc else "",
            status_url,
            number,
        )
        return twiml
