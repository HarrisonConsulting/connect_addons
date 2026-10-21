from odoo import fields, models


class ConnectCallerOwnNumber(models.Model):
    _name = 'connect.caller.own.number'
    _description = 'Our Number Reputation'
    _rec_name = 'e164'

    _e164_uniq = models.Constraint('UNIQUE (e164)', 'This number is already watched.')

    e164 = fields.Char(required=True, index=True, help='One of our numbers in E.164 form.')
    name = fields.Char(help='Friendly name of the number in Twilio.')
    kind = fields.Selection(
        [('did', 'Inbound number'), ('callerid', 'Verified caller ID')], required=True,
        help='did means an inbound number we rent. callerid means a verified outbound caller ID. '
             'Only DIDs count as spoof evidence.')
    twilio_number_id = fields.Many2one(
        'connect.twilio.number', ondelete='set null',
        help='Twilio number record this row mirrors.')
    callerid_id = fields.Many2one(
        'connect.twilio.outgoing_callerid', ondelete='set null',
        help='Verified caller ID record this row mirrors.')
    reputation_id = fields.Many2one(
        'connect.caller.reputation', ondelete='set null',
        help='How other systems label this number.')
    active_mark_count = fields.Integer(
        related='reputation_id.active_mark_count', help='Active reports about our number.')
    spam_score = fields.Float(
        related='reputation_id.spam_score',
        help='Weighted 0 to 1 spam score other systems give our number.')
    flagged_mark_ids = fields.Many2many(
        'connect.caller.mark', 'connect_caller_own_number_flagged_rel',
        'own_number_id', 'mark_id',
        help='External reports that appeared since staff last acknowledged.')
    state = fields.Selection(
        [('clean', 'Clean'), ('flagged', 'Flagged'), ('acknowledged', 'Acknowledged')],
        required=True, default='clean', index=True,
        help='flagged means a new external report has no acknowledgement yet.')
    acknowledged_by = fields.Many2one(
        'res.users', readonly=True, help='Staff member who acknowledged the latest reports.')
    acknowledged_at = fields.Datetime(
        readonly=True, help='When the latest reports were acknowledged.')
    last_refresh_at = fields.Datetime(
        help='When the reputation sources were last queued for this number.')
    active = fields.Boolean(
        default=True, help='Cleared when the number leaves the Twilio account.')
