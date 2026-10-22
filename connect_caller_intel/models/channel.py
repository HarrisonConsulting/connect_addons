import logging
from datetime import timedelta

from odoo import api, fields, models

from ..services import heuristics, numbering, stir
from .reputation import ATTESTATIONS

_logger = logging.getLogger(__name__)

BURST_WINDOW = timedelta(minutes=10)
THRESHOLD_PARAM = 'connect_caller_intel.threshold_%s'
SOURCE_XMLID = 'connect_caller_intel.source_%s'
ALERT_BY_VERDICT = {
    'likely_spoof': 'connect_spoof_alert',
    'likely_spam': 'connect_reputation_update',
}


class ConnectChannel(models.Model):
    _inherit = 'connect.channel'

    caller_attestation = fields.Selection(
        ATTESTATIONS, readonly=True,
        help='STIR/SHAKEN attestation the network reported when the call rang.')
    caller_cnam = fields.Char(
        readonly=True, help='Caller name the network presented. Untrusted text.')
    caller_region_reported = fields.Char(
        readonly=True, help='City, state and country the carrier reported for the caller.')
    caller_npanxx = fields.Char(
        index=True, readonly=True,
        help='Area code and exchange of the caller. Drives the same-exchange burst check.')
    caller_reputation_id = fields.Many2one(
        'connect.caller.reputation', readonly=True, ondelete='set null',
        help='Reputation row of the calling number.')

    def _map_twilio_params(self, params):
        """Keep the carrier evidence the core mapping drops."""
        generic = super()._map_twilio_params(params)
        region = ', '.join(filter(None, (
            params.get('FromCity'), params.get('FromState'), params.get('FromCountry'))))
        generic['caller_evidence'] = {
            'attestation': stir.parse_stir_verstat(params.get('StirVerstat'))['attestation'],
            'cnam': params.get('CallerName') or '',
            'region': region,
        }
        return generic

    @api.model
    def process_channel_event(self, params):
        created = bool(params.get('sid')) and not self.sudo().search_count(
            [('sid', '=', params['sid'])], limit=1)
        channel = super().process_channel_event(params)
        if created and channel and channel._caller_intel_is_external_ring():
            channel._caller_intel_ingest(params.get('caller_evidence') or {})
            channel._caller_intel_enqueue()
        return channel

    def _caller_intel_is_external_ring(self):
        self.ensure_one()
        return (self.technical_direction == 'inbound'
                and self.call_type == 'phone'
                and not self.parent_channel
                and not self.caller_pbx_user)

    def _caller_intel_ingest(self, evidence):
        """Assess the caller from local data only. Never raises: routing must go on."""
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                result = self._caller_intel_assess(evidence)
        except Exception:
            _logger.warning('Caller intel ingest failed for channel %s', self.id, exc_info=True)
            return False
        try:
            self._caller_intel_notify(result)
        except Exception:
            _logger.warning('Caller intel notification failed for channel %s', self.id, exc_info=True)
        return result

    def _caller_intel_thresholds(self):
        get_param = self.env['ir.config_parameter'].sudo().get_param
        thresholds = {}
        for key in heuristics.DEFAULTS:
            value = get_param(THRESHOLD_PARAM % key)
            if value:
                thresholds[key] = float(value) if key == 'spam_score' else int(value)
        return thresholds

    def _caller_intel_source_id(self, code):
        return self.env['ir.model.data']._xmlid_to_res_id(
            SOURCE_XMLID % code, raise_if_not_found=False) or False

    def _caller_intel_assess(self, evidence):
        channel = self.sudo().with_context(tracking_disable=True)
        Reputation = channel.env['connect.caller.reputation']
        now = fields.Datetime.now()
        since = now - BURST_WINDOW
        number = channel.caller_number
        anonymous = stir.is_anonymous_caller(channel.caller)
        facts_num = numbering.analyze(number) if number and not anonymous else {}
        e164 = facts_num.get('e164') or ''
        attestation = evidence.get('attestation') or 'none'
        cnam = (evidence.get('cnam') or '').strip()
        partner = channel.partner

        rep = Reputation.browse()
        history = {}
        marks = []
        extra_facts = {}
        if e164:
            rep = Reputation.search_fetch(
                [('e164', '=', e164)],
                ['first_seen', 'next_refresh_at', 'active_mark_count', 'spam_score',
                 'attest_a_count', 'attest_b_count', 'attest_c_count',
                 'attest_none_count', 'attest_failed_count'],
                limit=1) or Reputation._get_or_create(e164)
            history = {k: rep['attest_%s_count' % k.lower()] for k in ('A', 'B', 'C', 'none')}
            counter = 'attest_%s_count' % attestation.lower()
            vals = {'last_seen': now, 'last_attestation': attestation, counter: rep[counter] + 1}
            if cnam:
                vals['cnam_last'] = cnam
            if not rep.first_seen:
                vals.update({
                    'first_seen': now,
                    'country_code': facts_num['country_code'],
                    'region': evidence.get('region') or facts_num['region'],
                    'npanxx': facts_num['npanxx'],
                    'line_type': facts_num['line_type'],
                    'line_type_source_id': self._caller_intel_source_id('numbering_plan'),
                    'carrier_name': facts_num['carrier'],
                    'is_valid_number': facts_num['valid'],
                })
            extra_facts, extra_vals = channel._caller_intel_extra_facts(facts_num, rep, not rep.first_seen)
            vals.update(extra_vals)
            rep.write(vals)
            if rep.active_mark_count:
                marks = [{'source': m.source_id.code, 'report_count': m.report_count, 'active': True}
                         for m in rep.mark_ids if m.state == 'active']

        Channel = channel.env['connect.channel']
        npanxx = facts_num.get('npanxx', '')
        burst = 1
        snowshoe = 1
        if number and not anonymous:
            burst += Channel.search_count([
                ('caller_number', '=', number), ('technical_direction', '=', 'inbound'),
                ('id', '!=', channel.id), ('create_date', '>=', since)])
            if npanxx and not partner:
                groups = Channel._read_group(
                    [('caller_npanxx', '=', npanxx), ('partner', '=', False),
                     ('caller_number', '!=', number), ('create_date', '>=', since)],
                    groupby=['caller_number'])
                snowshoe += len(groups)

        names = []
        if partner:
            names = [n for n in {partner.name, partner.commercial_partner_id.name} if n]
        own_dids = channel._caller_intel_own_numbers()
        facts = {
            'e164': e164,
            'attestation': attestation,
            'anonymous': anonymous,
            'partner_match': bool(partner),
            'partner_names': names,
            'history': history,
            'marks': marks,
            'numbering': facts_num,
            'cnam': cnam,
            'own_dids': own_dids,
            'npanxx': npanxx,
            'burst_count': burst,
            'snowshoe_count': snowshoe,
            'spam_score': rep.spam_score if rep else 0.0,
        }
        facts.update(extra_facts)
        signals = heuristics.evaluate(facts, self._caller_intel_thresholds())
        verdict = heuristics.verdict(signals, bool(partner))
        if signals:
            channel.env['connect.caller.signal'].create([{
                'channel_id': channel.id,
                'reputation_id': rep.id,
                'code': s['code'],
                'severity': s['severity'],
                'reason': s['reason'],
                'source_id': self._caller_intel_source_id(s['source']) if s['source'] else False,
            } for s in signals])
        channel.write({
            'caller_attestation': attestation,
            'caller_cnam': cnam,
            'caller_region_reported': evidence.get('region') or '',
            'caller_npanxx': npanxx,
            'caller_reputation_id': rep.id,
        })
        return {'verdict': verdict, 'signals': signals}

    def _caller_intel_extra_facts(self, facts_num, rep, first_sight):
        """Hook for extra heuristic facts and reputation values; costs at most one query.

        Returns ``(facts, reputation_vals)``, merged before the reputation is written.
        """
        return {}, {}

    def _caller_intel_own_numbers(self):
        """E.164 numbers of ours, used to spot a caller that presents one of them."""
        return self.env['connect.twilio.number'].search_fetch(
            [], ['phone_number']).mapped('phone_number')

    def _caller_intel_notify(self, result):
        """Tell the ringing users on their own bus channel when the call looks bad."""
        message_type = ALERT_BY_VERDICT.get((result or {}).get('verdict'))
        if not message_type:
            return
        users = self.called_user | self.called_pbx_user.user
        payload = {
            'channel_sid': self.sid,
            'reputation_id': self.caller_reputation_id.id,
            'verdict': result['verdict'],
            'signals': [s['code'] for s in result['signals']],
        }
        for user in users:
            self.env['bus.bus']._sendone('connect_actions_%s' % user.id, message_type, payload)

    def _caller_intel_enqueue(self):
        """Queue the async lookups for a number whose cached answers have gone stale."""
        rep = self.caller_reputation_id
        if not rep or (rep.next_refresh_at and rep.next_refresh_at > fields.Datetime.now()):
            return
        try:
            with self.env.cr.savepoint():
                rep.sudo()._enqueue_stale_sources()
        except Exception:
            _logger.warning('Caller intel enqueue failed for %s', rep.e164, exc_info=True)
