from odoo import api, fields, models

ATTESTATIONS = [('A', 'A'), ('B', 'B'), ('C', 'C'), ('none', 'None'), ('failed', 'Failed')]


class ConnectCallerReputation(models.Model):
    _name = 'connect.caller.reputation'
    _description = 'Caller Number Reputation'
    _rec_name = 'e164'
    _order = 'last_seen desc, id desc'

    _e164_uniq = models.Constraint(
        'UNIQUE (e164)', 'A reputation already exists for this number.')

    e164 = fields.Char(required=True, index=True,
                       help='The caller number in E.164 form. The dedup key for every lookup and mark.')
    country_code = fields.Char(help='ISO country of the number from the numbering plan.')
    region = fields.Char(
        help='Geographic description from offline numbering metadata or the carrier-reported From city/state.')
    npanxx = fields.Char(
        index=True, help='NANP area code and exchange (first 6 national digits). '
                         'Drives neighbour-spoof and snowshoe checks.')
    line_type = fields.Selection(
        [('mobile', 'Mobile'), ('fixed', 'Fixed'), ('voip', 'VoIP'), ('toll_free', 'Toll free'),
         ('premium', 'Premium'), ('shared_cost', 'Shared cost'), ('unknown', 'Unknown')],
        default='unknown',
        help='Line type from offline metadata, overridden by a paid lookup when one is enabled.')
    line_type_source_id = fields.Many2one(
        'connect.caller.source', ondelete='set null', help='Which source set the line type.')
    carrier_name = fields.Char(help='Carrier name from offline metadata or lookup.')
    cnam_last = fields.Char(help='Last caller name the network presented. Untrusted text.')
    is_valid_number = fields.Boolean(
        default=True, help='False when the number is not a valid assigned pattern in the numbering plan.')
    attest_a_count = fields.Integer(help='Inbound calls from this number attested A.')
    attest_b_count = fields.Integer(help='Inbound calls from this number attested B.')
    attest_c_count = fields.Integer(help='Inbound calls from this number attested C.')
    attest_none_count = fields.Integer(help='Inbound calls from this number with no attestation.')
    attest_failed_count = fields.Integer(help='Inbound calls from this number that failed verification.')
    last_attestation = fields.Selection(
        ATTESTATIONS, help='Attestation on the most recent inbound call.')
    first_seen = fields.Datetime(help='First inbound call from this number.')
    last_seen = fields.Datetime(help='Most recent inbound call from this number.')
    mark_ids = fields.One2many(
        'connect.caller.mark', 'reputation_id',
        help='Spam and spoof reports, each naming the system that reported it.')
    active_mark_count = fields.Integer(
        compute='_compute_marks', store=True, help='Number of marks in state active.')
    spam_score = fields.Float(
        compute='_compute_marks', store=True,
        help='0 to 1 blend of active marks, weighted by report count.')
    source_checked_json = fields.Json(
        help='Per-source last checked time and outcome (found, not_found, error), '
             'so negative results are cached too.')
    next_refresh_at = fields.Datetime(
        index=True, help='Earliest moment any enabled async source goes stale for this number.')
    candidate_business_name = fields.Char(
        help='Business name suggested by async research. Never auto-linked to a partner.')

    @api.depends('mark_ids.state', 'mark_ids.report_count')
    def _compute_marks(self):
        for rep in self:
            active = rep.mark_ids.filtered(lambda m: m.state == 'active')
            rep.active_mark_count = len(active)
            miss = 1.0
            for mark in active:
                miss *= 1.0 - min(1.0, max(mark.report_count, 1) / 10.0)
            rep.spam_score = 1.0 - miss

    @api.model
    def _get_or_create(self, e164):
        """Return the single reputation for ``e164``, creating it race-safely."""
        rep = self.search([('e164', '=', e164)], limit=1)
        if rep:
            return rep
        try:
            with self.env.cr.savepoint():
                return self.create({'e164': e164})
        except Exception:
            rep = self.search([('e164', '=', e164)], limit=1)
            if not rep:
                raise
            return rep
