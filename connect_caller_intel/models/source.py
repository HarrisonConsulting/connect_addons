import math
from datetime import datetime, timezone

from psycopg2 import errors as pg_errors

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services.adapters.base import AdapterThrottled


class ConnectCallerSource(models.Model):
    _name = 'connect.caller.source'
    _description = 'Caller Intel Source'
    _order = 'tier, name'

    _code_uniq = models.Constraint(
        'UNIQUE (code)', 'A source code must be unique.')

    code = fields.Char(required=True, help='Stable adapter key, e.g. stir_shaken, ftc_dnc.')
    name = fields.Char(required=True, translate=True,
                       help='Name shown to staff and in the "flagged by" text.')
    tier = fields.Selection(
        [('sync', 'Sync'), ('async', 'Async')], required=True, default='sync',
        help='Sync sources run during ring on local data within a 300 ms budget. '
             'Async sources run on queue_job.')
    cost_class = fields.Selection(
        [('free', 'Free'), ('freemium', 'Freemium'), ('paid', 'Paid')],
        required=True, default='free',
        help='Whether a lookup costs money. Paid and freemium sources are off by default.')
    cost_note = fields.Text(
        help='Price per lookup and billing owner, shown in the confirmation before enabling.')
    enabled = fields.Boolean(
        readonly=True,
        help='Whether the pipeline consults this source. Changed only through Enable and Disable.')
    cost_acknowledged_by = fields.Many2one(
        'res.users', readonly=True,
        help='Who accepted the cost warning when enabling a paid source.')
    cost_acknowledged_at = fields.Datetime(
        readonly=True, help='When the cost warning was accepted.')
    credential_param = fields.Char(
        help='ir.config_parameter key that holds this source\'s API key. '
             'The key is never stored on the record.')
    ttl_hours = fields.Integer(
        help='How long a result, including "nothing found", stays fresh before a re-query.')
    clearing_url = fields.Char(help='Where a number owner disputes or clears a mark from this source.')
    clearing_note = fields.Text(
        help='Plain steps support can read to a customer to clear the mark.')
    attribution = fields.Char(help='Credit shown with every hint or mark from this source.')
    requires_purpose_ack = fields.Boolean(
        help='Enabling also requires accepting the permitted-purpose statement.')
    purpose_note = fields.Text(
        help='Permitted use of this source\'s data, shown before enabling.')
    purpose_acknowledged_by = fields.Many2one(
        'res.users', readonly=True, help='Who accepted the permitted-purpose statement.')
    purpose_acknowledged_at = fields.Datetime(
        readonly=True, help='When the permitted-purpose statement was accepted.')
    last_request_at = fields.Datetime(
        readonly=True, help='Last outbound request to this source. Paces the polite sources.')
    adapter_available = fields.Boolean(
        compute='_compute_adapter_available',
        help='False when the adapter or its Python dependency is missing. '
             'Such a source cannot be enabled.')

    def _compute_adapter_available(self):
        from ..services.adapters.registry import ADAPTERS
        for source in self:
            source.adapter_available = source.tier == 'sync' or (
                source.code in ADAPTERS and ADAPTERS[source.code].ready)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('enabled') and vals.get('cost_class', 'free') != 'free':
                self._check_cost_ack()
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('enabled'):
            paid = self.filtered(
                lambda s: (s.cost_class != 'free' or s.requires_purpose_ack) and not s.enabled)
            if paid:
                self._check_cost_ack()
        return super().write(vals)

    def _check_cost_ack(self):
        if not self.env.context.get('caller_intel_cost_ack'):
            raise UserError(_(
                'A paid source can only be enabled through Enable, after the cost is acknowledged.'))

    def action_enable(self):
        """Enable sources; a paid one records who accepted the cost and when."""
        for source in self:
            if not source.adapter_available:
                raise UserError(_('%s has no available adapter and cannot be enabled.', source.name))
            if source.requires_purpose_ack and not self.env.context.get('caller_intel_purpose_ack'):
                raise UserError(_(
                    '%s can only be enabled through Enable, after the permitted purpose is accepted.',
                    source.name))
            vals = {'enabled': True}
            if source.cost_class != 'free':
                vals.update(cost_acknowledged_by=self.env.user.id,
                            cost_acknowledged_at=fields.Datetime.now())
            if source.requires_purpose_ack:
                vals.update(purpose_acknowledged_by=self.env.user.id,
                            purpose_acknowledged_at=fields.Datetime.now())
            source.with_context(caller_intel_cost_ack=True).write(vals)
        return True

    def action_enable_with_purpose(self):
        """Enable sources after the admin accepted the permitted-purpose statement."""
        return self.with_context(caller_intel_purpose_ack=True).action_enable()

    def action_disable(self):
        """Disable sources and clear any recorded acknowledgements."""
        self.write({'enabled': False, 'cost_acknowledged_by': False,
                    'cost_acknowledged_at': False, 'purpose_acknowledged_by': False,
                    'purpose_acknowledged_at': False})
        return True

    @api.model
    def _caller_intel_throttle(self, code, min_interval):
        """Pace requests to ``code``: raise AdapterThrottled unless ``min_interval`` seconds have passed.

        The row lock serialises workers, so one second between requests holds across the pod.
        """
        try:
            with self.env.cr.savepoint():
                self.env.cr.execute(
                    'SELECT id, last_request_at FROM connect_caller_source '
                    'WHERE code = %s FOR UPDATE NOWAIT', [code])
                row = self.env.cr.fetchone()
                if not row:
                    return
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                if row[1]:
                    remaining = min_interval - (now - row[1]).total_seconds()
                    if remaining > 0:
                        raise AdapterThrottled(
                            'Source %s is paced' % code, seconds=math.ceil(remaining) or 1)
                self.env.cr.execute(
                    'UPDATE connect_caller_source SET last_request_at = %s WHERE id = %s',
                    [now, row[0]])
        except pg_errors.LockNotAvailable as exc:
            raise AdapterThrottled('Source %s is busy' % code, seconds=1) from exc
        self.browse(row[0]).invalidate_recordset(['last_request_at'])
