"""Caller intel settings."""

from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    caller_intel_threshold_suspicious = fields.Float(
        string='Suspicious from', config_parameter='connect_caller_intel.threshold_suspicious',
        default=0.3, help='Reputation score (0 to 1) from which a caller is shown as suspicious.')
    caller_intel_threshold_spam = fields.Float(
        string='Likely spam from', config_parameter='connect_caller_intel.threshold_spam',
        default=0.6, help='Reputation score (0 to 1) from which a caller is shown as likely spam.')
    caller_intel_paid_source_ids = fields.Many2many(
        'connect.caller.source', compute='_compute_caller_intel_paid_sources',
        help='Enabled sources that charge for each lookup.')

    @api.depends_context('uid')
    def _compute_caller_intel_paid_sources(self):
        paid = self.env['connect.caller.source'].search(
            [('enabled', '=', True), ('cost_class', '!=', 'free')])
        for settings in self:
            settings.caller_intel_paid_source_ids = paid
