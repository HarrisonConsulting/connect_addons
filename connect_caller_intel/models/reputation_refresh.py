import logging
from datetime import timedelta

from odoo import api, fields, models

from odoo.addons.queue_job.exception import RetryableJobError

from ..services.adapters.base import AdapterError, AdapterThrottled
from ..services.adapters.registry import ADAPTERS

_logger = logging.getLogger(__name__)

JOB_CHANNEL = 'root.connect_caller_intel'
PURGE_DAYS = 180
FTC_LOADED_PARAM = 'connect_caller_intel.ftc_loaded_through'
FTC_BACKFILL_DAYS = 2
FTC_MAX_DAYS_PER_RUN = 7
NUMBERING_KEYS = ('rate_center', 'numbering_state', 'ocn', 'ocn_company', 'nxx_status')


class ConnectCallerReputation(models.Model):
    _inherit = 'connect.caller.reputation'

    def _usable_async_sources(self, cost_classes=None):
        """Enabled, non-batch async sources whose adapter has its credential.

        ``cost_classes`` narrows the sources to those cost classes when given.
        """
        domain = [('enabled', '=', True), ('tier', '=', 'async')]
        if cost_classes is not None:
            domain.append(('cost_class', 'in', list(cost_classes)))
        sources = self.env['connect.caller.source'].sudo().search(domain)
        return sources.filtered(
            lambda s: s.code in ADAPTERS and not ADAPTERS[s.code].batch
            and ADAPTERS[s.code].available(self.env))

    def _source_is_fresh(self, source, now):
        checked = (self.source_checked_json or {}).get(source.code)
        if not checked or checked.get('outcome') == 'error':
            return False
        at = fields.Datetime.to_datetime(checked.get('at'))
        return bool(at) and at + timedelta(hours=source.ttl_hours or 0) > now

    def _enqueue_stale_sources(self, cost_classes=None):
        """One job per stale (number, source). The identity key dedups in-flight lookups."""
        now = fields.Datetime.now()
        sources = self._usable_async_sources(cost_classes)
        for rec in self:
            for source in sources:
                if rec._source_is_fresh(source, now) or not ADAPTERS[source.code].applies_to(self.env, rec):
                    continue
                rec.with_delay(
                    channel=JOB_CHANNEL,
                    identity_key='caller_intel:%s:%s' % (rec.e164, source.code),
                    description='Caller intel %s for %s' % (source.code, rec.e164),
                )._refresh_source(source.code)

    def _refresh_source(self, code):
        """Job body: query one source for one number and cache the outcome, negative included."""
        self.ensure_one()
        source = self.env['connect.caller.source'].sudo().search([('code', '=', code)], limit=1)
        adapter = ADAPTERS.get(code)
        if not source or not source.enabled or not adapter or not adapter.available(self.env):
            return 'skipped'
        if not adapter.applies_to(self.env, self):
            return 'skipped'
        try:
            result = adapter.lookup(self.env, self.e164)
        except AdapterThrottled as exc:
            raise RetryableJobError(str(exc), seconds=exc.seconds, ignore_retry=True) from exc
        except AdapterError as exc:
            self._record_outcome(code, 'error')
            raise RetryableJobError(str(exc)) from exc
        self._apply_result(source, result)
        self._record_outcome(code, 'found' if result.get('found') else 'not_found')
        return 'found' if result.get('found') else 'not_found'

    def _apply_result(self, source, result):
        Mark = self.env['connect.caller.mark'].sudo()
        for item in result.get('marks') or []:
            domain = [('reputation_id', '=', self.id), ('source_id', '=', source.id),
                      ('external_ref', '=', item['external_ref'])]
            vals = dict(item, reputation_id=self.id, source_id=source.id, state='active')
            existing = Mark.search(domain, limit=1)
            if existing:
                existing.write(vals)
            else:
                Mark.create(vals)
        if not result.get('found'):
            Mark.search([('reputation_id', '=', self.id), ('source_id', '=', source.id),
                         ('state', '=', 'active')]).write({'state': 'expired'})
        line = {}
        if result.get('line_type'):
            line.update(line_type=result['line_type'], line_type_source_id=source.id)
        if result.get('carrier'):
            line['carrier_name'] = result['carrier']
        if result.get('cnam'):
            line['cnam_last'] = result['cnam']
        line.update({key: result['numbering'][key]
                     for key in NUMBERING_KEYS if key in (result.get('numbering') or {})})
        if line:
            self.sudo().write(line)
        self._apply_identities(source, result)

    def _apply_identities(self, source, result):
        """Upsert the identity hints a source returned and follow the strongest one."""
        Identity = self.env['connect.caller.identity'].sudo()
        now = fields.Datetime.now()
        stored = [] if source.no_store else result.get('identities') or []
        for item in stored:
            domain = [('reputation_id', '=', self.id), ('source_id', '=', source.id),
                      ('external_ref', '=', item['external_ref'])]
            vals = {
                'reputation_id': self.id, 'source_id': source.id,
                'name': item['name'], 'kind': item.get('kind') or 'unknown',
                'address': item.get('address'), 'url': item.get('url'),
                'external_ref': item['external_ref'],
                'confidence': item.get('confidence', 0.0),
                'attribution': source.attribution or source.name,
                'fetched_at': now,
                'expires_at': now + timedelta(hours=item.get('ttl_hours') or source.ttl_hours or 0),
            }
            existing = Identity.search(domain, limit=1)
            if existing:
                existing.write(vals)
            else:
                Identity.create(vals)
        if source.no_store or not result.get('found'):
            Identity.search([('reputation_id', '=', self.id), ('source_id', '=', source.id)]).unlink()
        top = Identity.search([
            ('reputation_id', '=', self.id), ('expires_at', '>', now), ('source_id.no_store', '=', False)],
            order='confidence desc, id desc', limit=1)
        if top:
            self.sudo().candidate_business_name = top.name

    def _record_outcome(self, code, outcome):
        now = fields.Datetime.now()
        checked = dict(self.source_checked_json or {})
        checked[code] = {'at': fields.Datetime.to_string(now), 'outcome': outcome}
        self.sudo().write({
            'source_checked_json': checked,
            'next_refresh_at': self._next_refresh(checked),
        })

    def _next_refresh(self, checked):
        ttl = {s.code: s.ttl_hours for s in self._usable_async_sources()}
        times = []
        for code, hours in ttl.items():
            entry = checked.get(code)
            if not entry or entry.get('outcome') == 'error':
                return fields.Datetime.now()
            times.append(fields.Datetime.to_datetime(entry['at']) + timedelta(hours=hours or 0))
        return min(times) if times else False

    @api.model
    def _cron_expire_and_purge(self):
        """Expire marks whose per-number source check lapsed twice over; drop idle numbers."""
        now = fields.Datetime.now()
        Source = self.env['connect.caller.source'].sudo()
        for source in Source.search([('tier', '=', 'async'), ('ttl_hours', '>', 0)]):
            adapter = ADAPTERS.get(source.code)
            if adapter is None or adapter.batch:
                continue
            horizon = now - timedelta(hours=2 * source.ttl_hours)
            for mark in self.env['connect.caller.mark'].sudo().search([
                    ('source_id', '=', source.id), ('state', '=', 'active')]):
                checked = (mark.reputation_id.source_checked_json or {}).get(source.code)
                at = fields.Datetime.to_datetime(checked['at']) if checked else mark.create_date
                if at < horizon:
                    mark.state = 'expired'
        self.env['connect.caller.identity'].sudo().search([('expires_at', '<=', now)]).unlink()
        watched = self.env['connect.caller.own.number'].sudo().with_context(active_test=False).search([])
        self.sudo().search([
            ('active_mark_count', '=', 0),
            ('last_seen', '<', now - timedelta(days=PURGE_DAYS)),
            ('id', 'not in', watched.reputation_id.ids),
        ]).unlink()

    @api.model
    def _cron_ftc_bulk_load(self):
        """Load FTC reported-call complaints for each day not yet loaded, up to yesterday."""
        adapter = ADAPTERS['ftc_dnc']
        source = self.env['connect.caller.source'].sudo().search([('code', '=', 'ftc_dnc')], limit=1)
        if not source or not source.enabled or not adapter.available(self.env):
            return 0
        params = self.env['ir.config_parameter'].sudo()
        today = fields.Date.context_today(self)
        loaded = params.get_param(FTC_LOADED_PARAM)
        last = fields.Date.to_date(loaded) if loaded else today - timedelta(days=FTC_BACKFILL_DAYS)
        count = 0
        for day in list(adapter.days_between(last, today))[:FTC_MAX_DAYS_PER_RUN]:
            try:
                numbers = adapter.fetch_day(self.env, day)
            except AdapterError as exc:
                _logger.warning("FTC bulk load for %s failed: %s", day, exc)
                break
            for e164, entry in numbers.items():
                self._ftc_upsert(source, e164, day, entry)
                count += 1
            params.set_param(FTC_LOADED_PARAM, fields.Date.to_string(day))
        return count

    def _ftc_upsert(self, source, e164, day, entry):
        rep = self.sudo().search([('e164', '=', e164)], limit=1) or self.sudo().create({'e164': e164})
        Mark = self.env['connect.caller.mark'].sudo()
        mark = Mark.search([('reputation_id', '=', rep.id), ('source_id', '=', source.id),
                            ('external_ref', '=', 'ftc:' + e164)], limit=1)
        if mark:
            first = min(filter(None, [mark.first_reported, entry['first']]))
            last = max(filter(None, [mark.last_reported, entry['last']]))
            mark.write({'report_count': mark.report_count + entry['count'],
                        'first_reported': first, 'last_reported': last, 'state': 'active'})
        else:
            Mark.create({
                'reputation_id': rep.id, 'source_id': source.id,
                'reporter': 'FTC Do Not Call complaints',
                'category': 'robocall' if entry['robocall'] else 'spam',
                'report_count': entry['count'],
                'first_reported': entry['first'], 'last_reported': entry['last'],
                'evidence_url': 'https://www.ftc.gov/policy-notices/open-government/data-sets/do-not-call-data',
                'external_ref': 'ftc:' + e164,
            })
