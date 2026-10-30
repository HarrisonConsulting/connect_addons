from odoo import fields, models

from ..tools.textable_client import OUTCOMES

OUTCOME_SELECTION = [
    ('accepted', 'Accepted'),
    ('rejected_request', 'Rejected: request'),
    ('rejected_contact', 'Rejected: user or contact'),
    ('auth_failed', 'Authentication failed'),
    ('throttled', 'Throttled'),
    ('unreachable', 'Unreachable'),
    ('ambiguous', 'Ambiguous'),
]
assert tuple(key for key, _label in OUTCOME_SELECTION) == OUTCOMES


class TextableSend(models.Model):
    """Send intents, written through a separate cursor before the POST.

    The row survives a rollback of the transaction that asked for the send,
    so a retried cron or a second click finds it and does not post again.
    An empty outcome means the POST may be in flight or its result was lost:
    that send is never repeated.
    """
    _name = 'connect.textable.send'
    _description = 'Textable Send Intent'
    _order = 'id desc'
    _rec_name = 'send_key'

    send_key = fields.Char(
        required=True, readonly=True,
        help='Idempotency key: the caller\'s key, or one derived from sender, '
             'recipient, body, target record, user and a five-minute window. '
             'A key is bound to its sender, recipient and body.')
    state = fields.Selection(
        [('queued', 'In flight'), ('done', 'Outcome recorded')],
        required=True, default='queued', readonly=True,
        help='In flight until the outcome of the POST is recorded. An intent '
             'left in flight is never sent again.')
    number_id = fields.Many2one(
        'connect.textable.number', required=True, readonly=True,
        ondelete='restrict', index=True,
        help='The sender number whose token made the request.')
    company_id = fields.Many2one(
        'res.company', required=True, readonly=True, index=True,
        help='Company of the sender number.')
    from_number = fields.Char(
        readonly=True,
        help='Sender number, E.164.')
    to_number = fields.Char(
        readonly=True, index=True,
        help='Recipient number, E.164.')
    body_hash = fields.Char(
        readonly=True,
        help='sha256 of the message text. The text itself is on the message.')
    user_id = fields.Many2one(
        'res.users', readonly=True, ondelete='set null',
        help='The user the send was made as.')
    claimed_at = fields.Datetime(
        required=True, readonly=True, index=True, default=fields.Datetime.now,
        help='When the last POST under this key was claimed. The send caps '
             'and echo matching count from here, so a key posted again after '
             'a definite failure counts as a new send.')
    attempts = fields.Integer(
        default=1, readonly=True,
        help='POSTs made under this key. Only a definite failure lets a later '
             'request with the same key post again.')
    outcome = fields.Selection(
        OUTCOME_SELECTION, readonly=True, index=True,
        help='Transport outcome of the last POST. Accepted means Textable took '
             'the request, not that the handset received it.')
    http_status = fields.Integer(
        readonly=True,
        help='HTTP status of the last POST, when one came back.')
    detail = fields.Char(
        readonly=True,
        help='Short transport detail, for example "HTTP 404" or "read timeout".')
    finished_at = fields.Datetime(
        readonly=True,
        help='When the outcome was recorded.')
    echo_message_uid = fields.Char(
        string='Echo MessageID', readonly=True, index=True,
        help='MessageID of the relay echo matched to this intent.')
    echo_received_at = fields.Datetime(
        readonly=True,
        help='When the relay echo for this intent arrived.')
    message_id = fields.Many2one(
        'connect.message', compute='_compute_message_id',
        help='The Connect message recorded for this intent, if its '
             'transaction committed.')

    _send_key_unique = models.Constraint(
        'UNIQUE(send_key)',
        'A send intent with this key already exists.',
    )

    def _compute_message_id(self):
        Message = self.env['connect.message'].sudo()
        for rec in self:
            rec.message_id = Message.search(
                [('textable_send_key', '=', rec.send_key)], order='id desc', limit=1)
