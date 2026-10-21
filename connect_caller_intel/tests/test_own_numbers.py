import random
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

_SUFFIXES = random.sample(range(100, 200), 4)
DID, DUAL, CALLERID, OTHER = ('+1312555%04d' % suffix for suffix in _SUFFIXES)


@tagged('post_install', '-at_install')
class TestOwnNumbers(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Own = cls.env['connect.caller.own.number']
        cls.Rep = cls.env['connect.caller.reputation']
        cls.Mark = cls.env['connect.caller.mark']
        cls.ftc = cls.env.ref('connect_caller_intel.source_ftc_dnc')
        cls.staff = cls.env.ref('connect_caller_intel.source_staff')
        cls.env['connect.twilio.number'].create(
            [{'phone_number': DID, 'friendly_name': 'Main'},
             {'phone_number': DUAL, 'friendly_name': 'Dual'}])
        cls.env['connect.twilio.outgoing_callerid'].create(
            [{'friendly_name': 'Dual', 'number': DUAL},
             {'friendly_name': 'Mobile', 'number': CALLERID}])
        cls.Own._sync_numbers()
        cls.own = cls.Own.search([('e164', '=', DID)])

    def _mark(self, e164, source=None, ref='r1', **vals):
        rep = self.Rep._get_or_create(e164)
        return self.Mark.create(dict({
            'reputation_id': rep.id, 'source_id': (source or self.ftc).id,
            'reporter': 'FTC Do Not Call complaints', 'external_ref': ref}, **vals))

    def _toasts(self):
        return patch.object(type(self.env['bus.bus']), '_sendone')

    def test_sync_one_row_per_e164_did_wins(self):
        """A number that is both a DID and a caller ID is a single DID row."""
        rows = self.Own.search([('e164', 'in', [DID, DUAL, CALLERID])])
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows.filtered(lambda o: o.e164 == DUAL).kind, 'did')
        self.assertEqual(rows.filtered(lambda o: o.e164 == CALLERID).kind, 'callerid')
        self.assertTrue(all(rows.mapped('reputation_id')))

    def test_sync_archives_removed_numbers(self):
        """A number that leaves the Twilio account is archived, not deleted."""
        self.env['connect.twilio.outgoing_callerid'].search([('number', '=', CALLERID)]).unlink()
        self.Own._sync_numbers()
        row = self.Own.with_context(active_test=False).search([('e164', '=', CALLERID)])
        self.assertFalse(row.active)

    def test_new_external_mark_flags_and_notifies_each_admin_once(self):
        """The first FTC mark flags the number and toasts every admin; a repeat upsert is silent."""
        admins = self.env.ref('connect.group_admin').all_user_ids
        self.assertTrue(admins)
        with self._toasts() as sendone:
            mark = self._mark(DID)
            self.assertEqual(sendone.call_count, len(admins))
            message = sendone.call_args.args[2]
            self.assertTrue(message['sticky'])
            self.assertIn(DID, message['message'])
            mark.write({'state': 'active', 'report_count': 9})
            self.assertEqual(sendone.call_count, len(admins))
        self.assertEqual(self.own.state, 'flagged')
        self.assertEqual(self.own.flagged_mark_ids, mark)

    def test_staff_mark_never_flags(self):
        """A staff mark is an internal opinion, not an external report."""
        with self._toasts() as sendone:
            self._mark(DID, source=self.staff, reporter='Staff', ref='s1')
        sendone.assert_not_called()
        self.assertEqual(self.own.state, 'clean')

    def test_mark_on_foreign_number_does_not_search_own_numbers(self):
        """A mark on a number that is not ours costs no own-number query."""
        self.Own._watched_e164s()
        with patch.object(type(self.Own), 'search') as search, self._toasts() as sendone:
            self._mark(OTHER)
        search.assert_not_called()
        sendone.assert_not_called()
        with self.assertQueryCount(0):
            self.Own._watched_e164s()

    def test_acknowledge_then_new_mark_flags_again(self):
        """Acknowledging clears the flagged marks; a later report flags the number anew."""
        self._mark(DID, ref='a1')
        self.own.action_acknowledge()
        self.assertEqual(self.own.state, 'acknowledged')
        self.assertFalse(self.own.flagged_mark_ids)
        self.assertEqual(self.own.acknowledged_by, self.env.user)
        self._mark(DID, ref='a2')
        self.assertEqual(self.own.state, 'flagged')

    def test_clearing_every_flagged_mark_returns_to_clean(self):
        """When the last flagged mark goes inactive the number is clean and admins are told."""
        mark = self._mark(DID, ref='c1')
        with self._toasts() as sendone:
            mark.state = 'cleared'
            self.assertTrue(sendone.called)
        self.assertEqual(self.own.state, 'clean')
        self.assertFalse(self.own.flagged_mark_ids)

    def test_callerid_is_not_spoof_evidence_but_did_is(self):
        """Only DIDs are offered to the spoof heuristics."""
        own_dids = self.env['connect.channel']._caller_intel_own_numbers()
        self.assertIn(DID, own_dids)
        self.assertNotIn(CALLERID, own_dids)

    def test_refresh_without_paid_toggle_queues_free_sources_only(self):
        """The paid toggle off narrows the refresh to free sources."""
        self.env['ir.config_parameter'].sudo().set_param(
            'connect_caller_intel.own_number_refresh_paid', 'False')
        with patch.object(type(self.Rep), '_enqueue_stale_sources') as enqueue:
            self.own._refresh_reputation()
        enqueue.assert_called_once_with(cost_classes=('free',))
        self.assertTrue(self.own.last_refresh_at)

    def test_refresh_with_paid_toggle_queues_every_class(self):
        """The paid toggle on lifts the cost class filter."""
        self.env['ir.config_parameter'].sudo().set_param(
            'connect_caller_intel.own_number_refresh_paid', 'True')
        with patch.object(type(self.Rep), '_enqueue_stale_sources') as enqueue:
            self.own._refresh_reputation()
        enqueue.assert_called_once_with(cost_classes=None)
