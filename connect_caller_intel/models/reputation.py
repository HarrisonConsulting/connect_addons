from odoo import api, fields, models

from ..services import heuristics

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

    rate_center = fields.Char(
        help='Locality the exchange is assigned to, from NANPA or Local Calling Guide.')
    numbering_state = fields.Char(help='State or province the exchange serves.')
    ocn = fields.Char(
        help='Operating Company Number holding the exchange. Approximate after pooling or porting.')
    ocn_company = fields.Char(
        help='Carrier that holds the exchange per the numbering administrator. '
             'The number may have been ported since.')
    nxx_status = fields.Selection(
        [('assigned', 'Assigned'), ('unavailable', 'Unavailable'), ('available', 'Available'),
         ('unknown', 'Unknown')],
        default='unknown',
        help='Exchange status from the numbering administrator; unknown outside the US '
             'or before the first load.')
    identity_ids = fields.One2many(
        'connect.caller.identity', 'reputation_id',
        help='Unverified names public and paid sources associate with this number.')
    calling_from = fields.Char(
        compute='_compute_calling_from',
        help='Short "City, ST · carrier" line shown to staff.')

    @api.depends('rate_center', 'numbering_state', 'region', 'ocn_company', 'carrier_name')
    def _compute_calling_from(self):
        for rep in self:
            if rep.rate_center and rep.numbering_state:
                locality = '%s, %s' % (rep.rate_center.title(), rep.numbering_state)
            else:
                locality = rep.region
            rep.calling_from = ' · '.join(
                part for part in (locality, rep.ocn_company or rep.carrier_name) if part)

    @api.depends('mark_ids.state', 'mark_ids.report_count', 'mark_ids.source_id')
    def _compute_marks(self):
        for rep in self:
            active = rep.mark_ids.filtered(lambda m: m.state == 'active')
            rep.active_mark_count = len(active)
            miss = 1.0
            for mark in active:
                weight = heuristics.source_weight(mark.source_id.code)
                miss *= 1.0 - weight * min(1.0, max(mark.report_count, 1) / 10.0)
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
