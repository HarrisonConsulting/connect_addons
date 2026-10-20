import logging

from markupsafe import Markup, escape

from odoo import fields, models

from ..services import heuristics, numbering

_logger = logging.getLogger(__name__)

VERDICTS = [
    ('trusted', 'Trusted'),
    ('known', 'Known'),
    ('unknown', 'Unknown'),
    ('suspicious', 'Suspicious'),
    ('likely_spam', 'Likely spam'),
    ('likely_spoof', 'Likely spoof'),
]
VERDICT_LABELS = dict(VERDICTS)
SEVERITY_RISK = {'info': 0.0, 'warn': 0.5, 'high': 1.0}
QUIET_VERDICTS = ('trusted', 'known')
MISSED_STATUSES = ('no-answer', 'canceled', 'busy', 'failed')
# Calling codes and NANP area codes behind one-ring callback fraud.
WANGIRI_CALLING_CODES = frozenset({212, 216, 224, 225, 232, 252, 355, 371, 373, 375, 509, 591, 592, 597})
WANGIRI_NANP_AREAS = frozenset({'242', '246', '264', '268', '284', '345', '441', '473', '649', '664',
                                '721', '758', '767', '784', '809', '829', '849', '868', '869', '876'})


class ConnectCall(models.Model):
    _inherit = 'connect.call'

    caller_verdict = fields.Selection(
        VERDICTS, index=True, readonly=True,
        help='Verdict on the caller at ring time. It is not recomputed afterwards.')
    caller_risk_score = fields.Float(
        readonly=True, help='0 to 1 risk of the caller at ring time.')
    caller_intel_summary = fields.Char(
        readonly=True, help='Plain-text reason for the verdict, naming the systems that flagged the caller.')
    caller_signal_ids = fields.One2many(
        'connect.caller.signal', 'call_id', readonly=True,
        help='Heuristic hits raised while the call rang.')
    caller_reputation_id = fields.Many2one(
        'connect.caller.reputation', readonly=True, ondelete='set null',
        help='Reputation row of the calling number.')
    caller_commercial_partner_id = fields.Many2one(
        'res.partner', readonly=True, ondelete='set null',
        help='Company of the matched contact.')

    def process_call_event(self, channel, error_data=None):
        call_id = super().process_call_event(channel, error_data)
        if call_id:
            try:
                with self.env.cr.savepoint():
                    self.browse(call_id)._caller_intel_copy_snapshot(channel)
            except Exception:
                _logger.warning('Caller intel snapshot failed for call %s', call_id, exc_info=True)
        return call_id

    def _caller_intel_copy_snapshot(self, channel):
        """Freeze the inbound leg's assessment onto the call, once."""
        self.ensure_one()
        if self.caller_verdict or self.direction != 'incoming' or not channel._caller_intel_is_external_ring():
            return
        if not channel.caller_attestation:
            self.sudo().caller_verdict = 'unknown'
            return
        signals = self.env['connect.caller.signal'].sudo().search([('channel_id', '=', channel.id)])
        rep = channel.caller_reputation_id
        verdict = heuristics.verdict(
            [{'code': s.code, 'severity': s.severity} for s in signals], bool(channel.partner))
        risk = max([SEVERITY_RISK[s.severity] for s in signals] + [rep.spam_score if rep else 0.0])
        self.sudo().write({
            'caller_verdict': verdict,
            'caller_risk_score': risk,
            'caller_intel_summary': self._caller_intel_summary_text(signals, rep),
            'caller_reputation_id': rep.id,
            'caller_commercial_partner_id': channel.partner.commercial_partner_id.id,
        })
        signals.write({'call_id': self.id})

    def _caller_intel_active_marks(self):
        return self.caller_reputation_id.sudo().mark_ids.filtered(lambda m: m.state == 'active')

    def _caller_intel_summary_text(self, signals, rep):
        marks = rep.sudo().mark_ids.filtered(lambda m: m.state == 'active')
        if marks:
            return 'Flagged by ' + ', '.join(
                '%s (%s)' % (m.reporter, m.report_count) if m.report_count > 1 else m.reporter
                for m in marks)
        flagged = signals.filtered(lambda s: s.severity != 'info')
        return '; '.join(flagged.mapped('reason'))

    def _caller_intel_reporters(self):
        self.ensure_one()
        return [m.reporter for m in self._caller_intel_active_marks()]

    def _caller_intel_clearing_hint(self):
        self.ensure_one()
        hints = []
        for mark in self._caller_intel_active_marks():
            hint = mark.clearing_url or mark.source_id.clearing_note
            if hint and hint not in hints:
                hints.append(hint)
        return hints

    def _caller_intel_wangiri_signal(self):
        """Raise the one-ring callback signal on a missed, zero-length call from a risky prefix."""
        self.ensure_one()
        if (self.direction != 'incoming' or self.duration or self.status not in MISSED_STATUSES
                or not self.caller):
            return False
        facts = numbering.analyze(self.caller)
        risky = (facts['calling_code'] in WANGIRI_CALLING_CODES
                 or (facts['calling_code'] == 1 and facts['npanxx'][:3] in WANGIRI_NANP_AREAS))
        found = [s for s in heuristics.evaluate({'missed_zero_duration': True, 'risky_country': risky})
                 if s['code'] == 'wangiri_risk']
        if not found:
            return False
        Signal = self.env['connect.caller.signal'].sudo()
        existing = Signal.search([('call_id', '=', self.id), ('code', '=', 'wangiri_risk')], limit=1)
        return existing or Signal.create({
            'call_id': self.id,
            'reputation_id': self.caller_reputation_id.id,
            'code': 'wangiri_risk',
            'severity': found[0]['severity'],
            'reason': found[0]['reason'],
            'source_id': self.env['ir.model.data']._xmlid_to_res_id(
                'connect_caller_intel.source_numbering_plan', raise_if_not_found=False) or False,
        })

    def _caller_intel_missed_line(self):
        """One escaped line for the missed-call notice, or an empty Markup."""
        self.ensure_one()
        wangiri = self._caller_intel_wangiri_signal()
        flagged = self.caller_verdict and self.caller_verdict not in QUIET_VERDICTS
        if not (flagged or wangiri):
            return Markup('')
        parts = [Markup('<b>Caller check:</b> %s') % escape(VERDICT_LABELS.get(self.caller_verdict, 'Unchecked'))]
        reporters = self._caller_intel_reporters()
        if reporters:
            parts.append(Markup('flagged by %s') % escape(', '.join(reporters)))
        elif self.caller_intel_summary:
            parts.append(escape(self.caller_intel_summary))
        hints = self._caller_intel_clearing_hint()
        if hints:
            parts.append(Markup('to clear: %s') % escape('; '.join(hints)))
        if wangiri:
            parts.append(Markup('<b>Do not call back:</b> %s') % escape(wangiri.reason))
        return Markup('. ').join(parts)

    def register_call_post_message(self, obj, **kwargs):
        if obj._name == 'connect.call' and kwargs.get('subtype_xmlid') == 'mail.mt_comment':
            try:
                line = obj._caller_intel_missed_line()
                if line:
                    kwargs['body'] = Markup('%s<br/>%s') % (escape(kwargs.get('body') or ''), line)
            except Exception:
                _logger.warning('Caller intel missed-call enrichment failed', exc_info=True)
        return super().register_call_post_message(obj, **kwargs)

    def _caller_intel_staff_feedback(self, spam):
        self.ensure_one()
        rep = self.caller_reputation_id.sudo()
        if not rep:
            return False
        user = self.env.user
        Mark = self.env['connect.caller.mark'].sudo()
        now = fields.Datetime.now()
        if spam:
            source = self.env.ref('connect_caller_intel.source_staff')
            ref = 'staff:%s' % user.id
            vals = {'reporter': '%s (%s)' % (user.name, user.company_id.name), 'category': 'spam',
                    'state': 'active', 'last_reported': now, 'cleared_by': False}
            mark = Mark.search([('reputation_id', '=', rep.id), ('source_id', '=', source.id),
                                ('external_ref', '=', ref)], limit=1)
            if mark:
                mark.write(vals)
            else:
                Mark.create(dict(vals, reputation_id=rep.id, source_id=source.id, external_ref=ref,
                                 first_reported=now))
        else:
            rep.mark_ids.filtered(lambda m: m.state == 'active').write(
                {'state': 'cleared', 'cleared_by': user.id})
        self.caller_signal_ids.sudo().write({'state': 'confirmed' if spam else 'dismissed'})
        return True

    def action_caller_mark_spam(self):
        """Staff report: this caller is spam."""
        for call in self:
            call._caller_intel_staff_feedback(spam=True)
        return True

    def action_caller_mark_trusted(self):
        """Staff report: this caller is not spam. Active marks are cleared."""
        for call in self:
            call._caller_intel_staff_feedback(spam=False)
        return True
