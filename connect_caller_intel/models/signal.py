from odoo import fields, models

SIGNAL_CODES = [
    ('attest_failed', 'Attestation failed'),
    ('partner_unattested', 'Known partner unattested'),
    ('partner_attest_drop', 'Known partner attestation dropped'),
    ('cnam_mismatch', 'Caller name mismatch'),
    ('own_did_as_caller', 'Own number as caller'),
    ('neighbor_spoof', 'Neighbour spoof'),
    ('invalid_number', 'Invalid number'),
    ('anonymous', 'Anonymous caller'),
    ('burst_same_number', 'Burst from one number'),
    ('snowshoe_npanxx', 'Snowshoe exchange'),
    ('wangiri_risk', 'Wangiri risk'),
    ('toll_free_unattested', 'Toll free unattested'),
    ('reported_spam', 'Reported as spam'),
    ('staff_marked', 'Marked by staff'),
    ('trusted_known', 'Trusted known caller'),
]


class ConnectCallerSignal(models.Model):
    _name = 'connect.caller.signal'
    _description = 'Caller Heuristic Signal'
    _order = 'id desc'

    call_id = fields.Many2one(
        'connect.call', ondelete='cascade', index=True, help='Call this signal was raised on.')
    channel_id = fields.Many2one(
        'connect.channel', ondelete='cascade', help='Inbound leg that carried the evidence.')
    reputation_id = fields.Many2one(
        'connect.caller.reputation', ondelete='cascade', help='Number reputation the signal concerns.')
    code = fields.Selection(SIGNAL_CODES, required=True, help='Which heuristic fired.')
    severity = fields.Selection(
        [('info', 'Info'), ('warn', 'Warning'), ('high', 'High')], required=True, default='info',
        help='High changes the ring card and the missed-call notice.')
    reason = fields.Char(help='Escaped one-line explanation shown to staff.')
    source_id = fields.Many2one(
        'connect.caller.source', ondelete='set null', help='System whose data produced the signal.')
    state = fields.Selection(
        [('new', 'New'), ('confirmed', 'Confirmed'), ('dismissed', 'Dismissed')],
        default='new', required=True, help='Staff feedback on whether the signal was right.')
