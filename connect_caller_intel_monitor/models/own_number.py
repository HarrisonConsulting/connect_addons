import json

from odoo import fields, models

OPEN_STATES = ('new', 'acknowledged')
RELEVANCE = {'own_number_marked': 0.8, 'own_did_as_caller': 0.9}


class ConnectCallerOwnNumber(models.Model):
    _inherit = 'connect.caller.own.number'

    monitor_signal_id = fields.Many2one(
        'ai.monitor.signal', ondelete='set null', copy=False,
        help="Open admin signal for this number's reports.")

    def _caller_intel_admin_signal(self, kind, payload):
        super()._caller_intel_admin_signal(kind, payload)
        self.ensure_one()
        signal = self.monitor_signal_id.sudo()
        if kind == 'own_number_cleared':
            if signal.state in OPEN_STATES:
                signal.action_act()
            return
        if signal.state in OPEN_STATES:
            detail = json.loads(signal.detail or '{}')
            detail.setdefault('reports', []).append(payload)
            signal.write({'detail': json.dumps(detail)})
            signal.message_post(body=self._signal_line(payload))
            return
        self._open_signal(kind, payload)

    def _signal_line(self, payload):
        return 'Reported by %s (%s).' % (payload['reporter'], payload['category'])

    def _open_signal(self, kind, payload):
        monitor = self.env.ref('connect_caller_intel_monitor.monitor_own_numbers').sudo()
        name = 'Our number %s flagged by %s' % (self.e164, payload['reporter'])
        signal = self.env['ai.monitor.signal'].sudo().create({
            'monitor_id': monitor.id,
            'name': name,
            'summary': self._signal_line(payload),
            'relevance_score': RELEVANCE.get(kind, 0.8),
            'detail': json.dumps({'e164': self.e164, 'reports': [payload]}),
        })
        admins = self.env.ref('connect.group_admin').sudo().all_user_ids
        alert = self.env['modelnexus.notification.alert'].sudo().create_alert(
            'monitor_signal', name, severity='warning',
            source_model=self._name, source_id=self.id, detail=payload,
            user_ids=admins.ids,
            dedup_key='caller_intel:own:%s:%s' % (self.e164, signal.id))
        signal.alert_id = alert
        self.monitor_signal_id = signal
