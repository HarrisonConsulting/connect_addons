import json

from odoo.tests import TransactionCase, tagged

E164 = '+15550100177'


def payload(reporter='Hiya', mark_id=1):
    return {
        'kind': 'own_number_marked', 'e164': E164, 'mark_id': mark_id,
        'reporter': reporter, 'category': 'spam', 'source_code': 'hiya',
        'clearing_url': '', 'clearing_note': '', 'remediation_code': '',
    }


@tagged('post_install', '-at_install')
class TestMonitorSignal(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.own = cls.env['connect.caller.own.number'].create(
            {'e164': E164, 'kind': 'did'})
        cls.Signal = cls.env['ai.monitor.signal']

    def _raise(self, kind='own_number_marked', data=None):
        self.own._caller_intel_admin_signal(kind, data or payload())

    def test_monitor_is_paused_api(self):
        monitor = self.env.ref('connect_caller_intel_monitor.monitor_own_numbers')
        self.assertEqual((monitor.state, monitor.source_type), ('paused', 'api'))

    def test_first_mark_opens_signal_and_alert(self):
        self._raise()
        signal = self.own.monitor_signal_id
        self.assertEqual(signal.state, 'new')
        self.assertEqual(signal.name, 'Our number %s flagged by Hiya' % E164)
        self.assertEqual(signal.relevance_score, 0.8)
        self.assertEqual(len(json.loads(signal.detail)['reports']), 1)
        self.assertEqual(signal.alert_id.alert_type, 'monitor_signal')
        self.assertTrue(signal.alert_id.user_ids)

    def test_second_mark_appends_to_open_signal(self):
        self._raise()
        signal = self.own.monitor_signal_id
        self._raise(data=payload('Truecaller', 2))
        self.assertEqual(self.own.monitor_signal_id, signal)
        self.assertEqual(self.Signal.search_count([('monitor_id', '=', signal.monitor_id.id)]), 1)
        self.assertEqual(len(json.loads(signal.detail)['reports']), 2)

    def test_acknowledged_signal_still_dedupes(self):
        self._raise()
        signal = self.own.monitor_signal_id
        signal.action_acknowledge()
        self._raise(data=payload('Truecaller', 2))
        self.assertEqual(self.own.monitor_signal_id, signal)

    def test_clearing_closes_signal_and_next_mark_opens_new(self):
        self._raise()
        signal = self.own.monitor_signal_id
        self._raise('own_number_cleared', {'kind': 'own_number_cleared', 'e164': E164})
        self.assertEqual(signal.state, 'acted_on')
        self._raise(data=payload('Hiya', 3))
        self.assertNotEqual(self.own.monitor_signal_id, signal)
        self.assertEqual(self.own.monitor_signal_id.state, 'new')
