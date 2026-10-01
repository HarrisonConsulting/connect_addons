from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class EndpointPayload(models.Model):
    _inherit = 'connect.endpoint'

    def caller_payload(self, account):
        self.ensure_one()
        if account.endpoint_id != self:
            raise ValidationError(
                'Account %s does not belong to endpoint %s.' % (
                    account.display_name, self.display_name))
        # This cut never opens a partner, even when the line has a person.
        return {
            'endpoint_id': self.id,
            'endpoint_name': self.name,
            'account_id': account.id,
            'slot': account.slot,
            'tool_id': account.tool_id.id,
            'tool_name': account.tool_id.name,
            'registration': account.registration,
            'partner': False,
            'actions': [],
        }


class UserLines(models.Model):
    _inherit = 'connect.user'

    account_desk_ids = fields.Many2many(
        'connect.endpoint.account',
        compute='_compute_account_desk_ids',
        string='Lines',
        store=False,
        help='',
    )

    @api.depends(
        'tool_assignment_ids.tool_id',
        'tool_assignment_ids.use',
        'tool_assignment_ids.role',
        'tool_assignment_ids.sequence',
    )
    def _compute_account_desk_ids(self):
        for user in self:
            user.account_desk_ids = user.desk_accounts()

    def desk_accounts(self):
        """Accounts assigned to this person, in voice-tool order then slot.

        A shared phone's other people, and a line with no person, stay off
        this list.
        """
        self.ensure_one()
        tools = self.ordered_tools('voice').mapped('tool_id')
        accounts = self.env['connect.endpoint.account'].search([
            ('user_id', '=', self.id),
            ('tool_id', 'in', tools.ids),
        ])
        rank = {tool.id: index for index, tool in enumerate(tools)}
        return accounts.sorted(
            key=lambda account: (
                rank.get(account.tool_id.id, len(rank)), account.slot or 0))

    def place_desk_call(self, account=None):
        """Choose the registered desk account. Does not originate the call."""
        self.ensure_one()
        registered = self.desk_accounts().filtered(
            lambda line: line.registration == 'registered')
        if account:
            if account not in registered:
                raise UserError(
                    '%s slot %s is not a registered line for this person.' % (
                        account.tool_id.name or account.display_name,
                        account.slot))
            return account
        if len(registered) == 1:
            return registered
        if not registered:
            names = ', '.join(self.ordered_tools('voice').mapped('name'))
            raise UserError(
                'No registered line for %s.' % (
                    names or 'the assigned voice tools'))
        choices = ', '.join(
            '%s slot %s' % (line.tool_id.name, line.slot)
            for line in registered)
        raise UserError('Which line? %s.' % choices)

    def browser_softphone_on(self):
        settings = self.env['connect.settings']
        if 'webrtc_provider' not in settings._fields:
            return False
        rows = settings.sudo().search([])
        if not rows:
            return False
        provider = rows[0].webrtc_provider
        return bool(provider) and provider != 'disabled'
