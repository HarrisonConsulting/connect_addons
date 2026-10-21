import logging

from markupsafe import escape

from odoo import Command, api, fields, models, tools

from ..services import numbering

_logger = logging.getLogger(__name__)

NOT_EXTERNAL_SOURCES = ('staff', 'local_partner')
PAID_REFRESH_PARAM = 'connect_caller_intel.own_number_refresh_paid'


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

    @api.model
    @tools.ormcache()
    def _own_did_e164s(self):
        """E.164 numbers of our active inbound DIDs."""
        return tuple(self.sudo().search_fetch(
            [('kind', '=', 'did')], ['e164']).mapped('e164'))

    @api.model
    @tools.ormcache()
    def _watched_e164s(self):
        """E.164 numbers of every active watched number."""
        return frozenset(self.sudo().search_fetch([], ['e164']).mapped('e164'))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        self.env.registry.clear_cache()
        return records

    def write(self, vals):
        result = super().write(vals)
        self.env.registry.clear_cache()
        return result

    def unlink(self):
        result = super().unlink()
        self.env.registry.clear_cache()
        return result

    remediation_ids = fields.Many2many(
        'connect.caller.remediation', compute='_compute_remediation_ids',
        help="Clearing guidance for this number's reports, plus standing registrations.")

    @api.depends('e164', 'flagged_mark_ids.remediation_id')
    def _compute_remediation_ids(self):
        standing = self.env['connect.caller.remediation'].sudo().search(
            [('applies_to_own_numbers', '=', True)])
        for own in self:
            country = numbering.analyze(own.e164)['country_code']
            own.remediation_ids = own.flagged_mark_ids.remediation_id | standing.filtered(
                lambda r: not r.country_codes
                or country in [c.upper() for c in _split(r.country_codes)])

    # -- sync and refresh ----------------------------------------------------

    @api.model
    def _cron_sync_and_refresh(self):
        """Mirror the Twilio numbers, then queue the reputation lookups for each."""
        numbers = self._sync_numbers()
        numbers._refresh_reputation()
        return len(numbers)

    @api.model
    def _sync_numbers(self):
        """Upsert one row per distinct E.164 and archive rows whose number is gone."""
        Own = self.sudo().with_context(active_test=False)
        wanted = {}
        for callerid in self.env['connect.twilio.outgoing_callerid'].sudo().search_fetch(
                [], ['number', 'friendly_name']):
            e164 = numbering.analyze(callerid.number)['e164']
            if e164:
                wanted[e164] = {
                    'kind': 'callerid', 'name': callerid.friendly_name,
                    'twilio_number_id': False, 'callerid_id': callerid.id}
        for did in self.env['connect.twilio.number'].sudo().search_fetch(
                [], ['phone_number', 'friendly_name']):
            e164 = numbering.analyze(did.phone_number)['e164']
            if e164:
                wanted[e164] = {
                    'kind': 'did', 'name': did.friendly_name,
                    'twilio_number_id': did.id,
                    'callerid_id': wanted.get(e164, {}).get('callerid_id', False)}
        existing = {own.e164: own for own in Own.search([])}
        for e164, vals in wanted.items():
            own = existing.get(e164)
            vals = dict(vals, active=True)
            if not own:
                Own.create(dict(vals, e164=e164))
            else:
                changed = {k: v for k, v in vals.items()
                           if (own[k].id if k.endswith('_id') else own[k]) != v and (own[k] or v)}
                if changed:
                    own.write(changed)
        gone = Own.browse([own.id for e164, own in existing.items()
                           if e164 not in wanted and own.active])
        if gone:
            gone.write({'active': False})
        active = Own.search([('active', '=', True)])
        for own in active.filtered(lambda o: not o.reputation_id):
            own.reputation_id = self.env['connect.caller.reputation'].sudo()._get_or_create(own.e164)
        return active

    def _refresh_reputation(self):
        """Queue the stale reputation sources for these numbers; paid ones only when allowed."""
        value = self.env['ir.config_parameter'].sudo().get_param(PAID_REFRESH_PARAM)
        paid_ok = str(value).lower() in ('true', '1')
        self.reputation_id.sudo()._enqueue_stale_sources(
            cost_classes=None if paid_ok else ('free',))
        self.sudo().write({'last_refresh_at': fields.Datetime.now()})

    def action_refresh_now(self):
        self.check_access('write')
        self._sync_numbers()
        self.filtered('active')._refresh_reputation()

    def action_acknowledge(self):
        self.check_access('write')
        self.filtered(lambda o: o.state == 'flagged').write({
            'state': 'acknowledged',
            'acknowledged_by': self.env.user.id,
            'acknowledged_at': fields.Datetime.now(),
            'flagged_mark_ids': [Command.clear()],
        })

    # -- alerts --------------------------------------------------------------

    def _on_new_external_marks(self, marks):
        """Flag this number for the marks it has not been told about yet."""
        self.ensure_one()
        fresh = marks - self.flagged_mark_ids
        if not fresh:
            return
        self.write({
            'state': 'flagged',
            'flagged_mark_ids': [Command.link(mark_id) for mark_id in fresh.ids],
        })
        for mark in fresh:
            remediation = mark.remediation_id
            note = mark.source_id.clearing_note
            self._caller_intel_admin_signal('own_number_marked', {
                'kind': 'own_number_marked',
                'e164': self.e164,
                'own_number_id': self.id,
                'mark_id': mark.id,
                'reporter': mark.reporter,
                'category': mark.category,
                'source_code': mark.source_id.code,
                'clearing_url': remediation.dispute_url or mark.clearing_url or '',
                'clearing_note': note or '',
                'remediation_code': remediation.code or '',
            })

    def _on_marks_inactive(self, marks):
        """Return a flagged number to clean once none of its flagged marks is active."""
        self.ensure_one()
        if self.state != 'flagged' or not (self.flagged_mark_ids & marks):
            return
        if any(mark.state == 'active' for mark in self.flagged_mark_ids):
            return
        self.write({'state': 'clean', 'flagged_mark_ids': [Command.clear()]})
        self._caller_intel_admin_signal('own_number_cleared', {
            'kind': 'own_number_cleared', 'e164': self.e164, 'own_number_id': self.id})

    def _caller_intel_admin_signal(self, kind, payload):
        """Toast every Connect admin; bridges extend this with durable signals."""
        self.ensure_one()
        if kind == 'own_number_cleared':
            message = 'Our number %s has no active reports left.' % payload['e164']
            sticky = warning = False
        else:
            how = payload.get('clearing_url') or payload.get('clearing_note') or ''
            message = 'Our number %s is now flagged by %s (%s). Clear it: %s' % (
                payload['e164'], payload['reporter'], payload['category'], how)
            sticky = warning = True
        message = str(escape(message))
        admins = self.env.ref('connect.group_admin').sudo().all_user_ids
        for user in admins:
            user.connect_notify(
                message, title='Caller intel', notify_uid=user.id,
                sticky=sticky, warning=warning)


def _split(text):
    return [part.strip() for part in (text or '').split(',') if part.strip()]


class ConnectCallerMark(models.Model):
    _inherit = 'connect.caller.mark'

    @api.model_create_multi
    def create(self, vals_list):
        marks = super().create(vals_list)
        marks.filtered(lambda m: m.state == 'active')._flag_own_numbers()
        return marks

    def write(self, vals):
        if 'state' not in vals:
            return super().write(vals)
        before = {mark.id: mark.state for mark in self}
        result = super().write(vals)
        self.filtered(lambda m: m.state == 'active' and before[m.id] != 'active')._flag_own_numbers()
        self.filtered(lambda m: m.state != 'active' and before[m.id] == 'active')._unflag_own_numbers()
        return result

    def _own_number_marks(self):
        """Marks about a watched number, as {own number: marks}; no query without watched numbers."""
        Own = self.env['connect.caller.own.number']
        watched = Own._watched_e164s()
        if not watched or not self:
            return {}
        marks = self.filtered(lambda m: m.reputation_id.e164 in watched)
        if not marks:
            return {}
        own_numbers = Own.sudo().search([('e164', 'in', marks.reputation_id.mapped('e164'))])
        return {own: marks.filtered(lambda m: m.reputation_id.e164 == own.e164)
                for own in own_numbers}

    def _flag_own_numbers(self):
        external = self.filtered(lambda m: m.source_id.code not in NOT_EXTERNAL_SOURCES)
        for own, marks in external._own_number_marks().items():
            own._on_new_external_marks(marks)

    def _unflag_own_numbers(self):
        for own, marks in self._own_number_marks().items():
            own._on_marks_inactive(marks)


class ConnectChannel(models.Model):
    _inherit = 'connect.channel'

    def _caller_intel_own_numbers(self):
        dids = self.env['connect.caller.own.number']._own_did_e164s()
        return list(dids) if dids else super()._caller_intel_own_numbers()


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    caller_intel_own_number_refresh_paid = fields.Boolean(
        string='Query paid sources for our numbers', config_parameter=PAID_REFRESH_PARAM,
        help='Also query paid sources when refreshing our own numbers.')
