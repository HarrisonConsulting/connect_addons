from odoo import api, fields, models


class ConnectCallerMark(models.Model):
    _name = 'connect.caller.mark'
    _description = 'Caller Report'
    _order = 'last_reported desc, id desc'

    _external_ref_uniq = models.Constraint(
        'UNIQUE (source_id, reputation_id, external_ref)',
        'This report is already recorded for the source.')

    reputation_id = fields.Many2one(
        'connect.caller.reputation', required=True, ondelete='cascade', index=True,
        help='Number this report concerns.')
    source_id = fields.Many2one(
        'connect.caller.source', required=True, ondelete='restrict',
        help='Integration that retrieved the report.')
    reporter = fields.Char(
        required=True, help='System or body that made the report. This is the "flagged by" text.')
    category = fields.Selection(
        [('spam', 'Spam'), ('robocall', 'Robocall'), ('scam', 'Scam'),
         ('spoof_suspect', 'Suspected spoof'), ('telemarketer', 'Telemarketer'),
         ('debt_collector', 'Debt collector'), ('political', 'Political'),
         ('survey', 'Survey'), ('other', 'Other')],
        default='spam', help='Kind of report.')
    report_count = fields.Integer(default=1, help='Reports this reporter holds for the number.')
    first_reported = fields.Datetime(help='First report by this reporter.')
    last_reported = fields.Datetime(help='Latest report by this reporter.')
    evidence_url = fields.Char(help='Public page or dataset row behind the report.')
    clearing_url = fields.Char(
        compute='_compute_clearing_url', store=True, readonly=False,
        help='Where the owner disputes this mark.')
    state = fields.Selection(
        [('active', 'Active'), ('expired', 'Expired'), ('cleared', 'Cleared'),
         ('disputed', 'Disputed')],
        default='active', required=True,
        help='Expired means past TTL with no refresh. Cleared means the reporter removed it '
             'or staff overrode it.')
    cleared_by = fields.Many2one(
        'res.users', help='Staff member who cleared the mark.')
    external_ref = fields.Char(help='Reporter-side id, used to upsert instead of duplicating.')

    @api.depends('source_id.clearing_url')
    def _compute_clearing_url(self):
        for mark in self:
            mark.clearing_url = mark.source_id.clearing_url
