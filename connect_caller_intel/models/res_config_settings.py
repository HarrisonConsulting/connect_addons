"""Caller intel settings."""

from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    caller_intel_threshold_spam_score = fields.Float(
        string='Spam score from', config_parameter='connect_caller_intel.threshold_spam_score',
        default=0.5, help='Reputation score (0 to 1) from which a caller is reported as spam.')
    caller_intel_threshold_mark_reports = fields.Integer(
        string='Reports from', config_parameter='connect_caller_intel.threshold_mark_reports',
        default=5, help='Number of reports on one mark from which a caller is reported as spam.')
    caller_intel_threshold_burst_calls = fields.Integer(
        string='Repeat calls from', config_parameter='connect_caller_intel.threshold_burst_calls',
        default=3, help='Calls from one number within 10 minutes that raise a burst signal.')
    caller_intel_paid_source_ids = fields.Many2many(
        'connect.caller.source', compute='_compute_caller_intel_paid_sources',
        help='Enabled sources that charge for each lookup.')

    @api.depends_context('uid')
    def _compute_caller_intel_paid_sources(self):
        paid = self.env['connect.caller.source'].search(
            [('enabled', '=', True), ('cost_class', '!=', 'free')])
        for settings in self:
            settings.caller_intel_paid_source_ids = paid
