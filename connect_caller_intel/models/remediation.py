from odoo import api, fields, models


def _split(text):
    return [part.strip() for part in (text or '').split(',') if part.strip()]


class ConnectCallerRemediation(models.Model):
    _name = 'connect.caller.remediation'
    _description = 'Caller Mark Clearing Guidance'
    _order = 'sequence, id'

    _code_uniq = models.Constraint('UNIQUE (code)', 'A remediation code must be unique.')

    code = fields.Char(required=True, help='Stable key, e.g. free_caller_registry.')
    name = fields.Char(
        required=True, translate=True,
        help='Analytics engine, dataset or board that applies the label.')
    engine_kind = fields.Selection(
        [('analytics_engine', 'Analytics engine'), ('complaint_dataset', 'Complaint dataset'),
         ('complaint_board', 'Complaint board'), ('data_provider', 'Data provider')],
        help='What kind of system applies the label.')
    carriers = fields.Char(
        help='Carriers or apps that show this engine\'s labels, e.g. "AT&T, T-Mobile, Verizon".')
    dispute_url = fields.Char(
        help='First-party page where the number\'s owner disputes or registers.')
    steps = fields.Text(
        translate=True, help='Plain steps support can read aloud to a customer.')
    removable = fields.Boolean(
        default=True,
        help='Cleared when no removal exists, as for FTC and FCC complaints, which are public record.')
    propagation_note = fields.Char(
        help='How long a correction takes to reach handsets, when the engine says.')
    source_codes = fields.Char(
        help='Comma-separated source codes whose marks this guidance covers.')
    match_reporters = fields.Char(
        help='Comma-separated reporter names or domains (case-insensitive, exact) '
             'this guidance covers.')
    country_codes = fields.Char(
        help='Comma-separated ISO countries the engine serves. Empty means all.')
    applies_to_own_numbers = fields.Boolean(
        help='Shown on Our Numbers as a standing registration step, even with no mark.')
    sequence = fields.Integer(default=10, help='Order shown to staff and customers.')
    active = fields.Boolean(default=True, help='Hides retired guidance.')


class ConnectCallerMark(models.Model):
    _inherit = 'connect.caller.mark'

    remediation_id = fields.Many2one(
        'connect.caller.remediation', compute='_compute_remediation_id',
        help='Clearing guidance for this report.')

    @api.depends('reporter', 'source_id.code')
    def _compute_remediation_id(self):
        by_reporter = {}
        by_source = {}
        for remediation in self.env['connect.caller.remediation'].sudo().search([]):
            for name in _split(remediation.match_reporters):
                by_reporter.setdefault(name.lower(), remediation)
            for code in _split(remediation.source_codes):
                by_source.setdefault(code, remediation)
        for mark in self:
            mark.remediation_id = (
                by_reporter.get((mark.reporter or '').lower())
                or by_source.get(mark.source_id.code)
                or False)
