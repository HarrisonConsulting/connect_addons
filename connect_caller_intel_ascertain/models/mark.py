from odoo import fields, models


class ConnectCallerMark(models.Model):
    _inherit = 'connect.caller.mark'

    inquiry_id = fields.Many2one(
        'modelnexus.ascertain.inquiry', ondelete='set null', index=True, readonly=True,
        help='Research inquiry that produced this report.')
    capture_id = fields.Many2one(
        'modelnexus.ascertain.capture', ondelete='set null', index=True, readonly=True,
        help='Captured page this report was read from.')
