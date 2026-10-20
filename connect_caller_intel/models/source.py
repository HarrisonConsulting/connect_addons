from odoo import _, api, fields, models
from odoo.exceptions import UserError


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
    adapter_available = fields.Boolean(
        compute='_compute_adapter_available',
        help='False when the adapter or its Python dependency is missing. '
             'Such a source cannot be enabled.')

    def _compute_adapter_available(self):
        from ..services import adapters
        registry = getattr(adapters, 'ADAPTERS', {})
        for source in self:
            source.adapter_available = source.tier == 'sync' or source.code in registry

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('enabled') and vals.get('cost_class', 'free') != 'free':
                self._check_cost_ack()
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('enabled'):
            paid = self.filtered(lambda s: s.cost_class != 'free' and not s.enabled)
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
            vals = {'enabled': True}
            if source.cost_class != 'free':
                vals.update(cost_acknowledged_by=self.env.user.id,
                            cost_acknowledged_at=fields.Datetime.now())
            source.with_context(caller_intel_cost_ack=True).write(vals)
        return True

    def action_disable(self):
        """Disable sources and clear any recorded cost acknowledgement."""
        self.write({'enabled': False, 'cost_acknowledged_by': False,
                    'cost_acknowledged_at': False})
        return True
