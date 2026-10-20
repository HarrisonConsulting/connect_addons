from odoo import fields, models

from ..services.web_reports import SOURCE_CODE


class ConnectCallerReputation(models.Model):
    _inherit = 'connect.caller.reputation'

    web_inquiry_id = fields.Many2one(
        'modelnexus.ascertain.inquiry', ondelete='set null', readonly=True,
        help='Latest web research inquiry run for this number.')
    web_capture_id = fields.Many2one(
        'modelnexus.ascertain.capture', ondelete='set null', readonly=True,
        help='First complaint-board page captured by the latest web research inquiry.')

    def _apply_result(self, source, result):
        super()._apply_result(source, result)
        if source.code == SOURCE_CODE and result.get('inquiry_id'):
            self.sudo().write({
                'web_inquiry_id': result['inquiry_id'],
                'web_capture_id': result.get('capture_id') or False,
            })
