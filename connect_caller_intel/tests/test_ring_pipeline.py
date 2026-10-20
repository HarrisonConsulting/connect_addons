from unittest.mock import ANY, patch

from odoo.tests import TransactionCase, new_test_user, tagged

TRUSTED_NUMBER = '+14155552671'
SPOOF_NUMBER = '+14155552672'
SPAM_NUMBER = '+14155552673'
WARM_NUMBER = '+14155552674'
OWN_DID = '+13125551234'


@tagged('post_install', '-at_install')
class TestRingPipeline(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Channel = cls.env['connect.channel']
        cls.Call = cls.env['connect.call']
        cls.partner = cls.env['res.partner'].create({'name': 'Acme Plumbing', 'phone': TRUSTED_NUMBER})
        cls.sid_seq = 0

    def _sid(self):
        type(self).sid_seq += 1
        return 'CAring%04d' % self.sid_seq

    def _params(self, caller, **extra):
        params = {
            'CallSid': self._sid(), 'Caller': caller, 'Called': OWN_DID, 'To': OWN_DID,
            'Direction': 'inbound', 'CallStatus': 'initiated',
        }
        params.update(extra)
        return params

    def _ring(self, caller, partner=None, **extra):
        """Post a Twilio inbound webhook through the real adapter chain."""
        License = type(self.env['oduist.license'])
        Partner = type(self.env['res.partner'])
        found = partner or self.env['res.partner']
        with patch.object(License, 'check_license', return_value=True), \
                patch.object(Partner, 'get_partner_by_number', return_value=found):
            call_id = self.Call.on_call_status(self._params(caller, **extra))
        return self.Call.browse(call_id)

    def _channel(self, caller, **vals):
        return self.Channel.create(dict({
            'sid': self._sid(), 'caller': caller, 'called': OWN_DID,
            'technical_direction': 'inbound', 'status': 'ringing'}, **vals))

    def test_attested_contact_is_stored_and_trusted(self):
        """StirVerstat passed-A from a known contact stores attestation, a signal and a trusted verdict."""
        call = self._ring(TRUSTED_NUMBER, partner=self.partner, StirVerstat='TN-Validation-Passed-A',
                          CallerName='ACME PLUMBING')
        channel = call.channels
        self.assertEqual(channel.caller_attestation, 'A')
        self.assertEqual(channel.caller_cnam, 'ACME PLUMBING')
        self.assertEqual(channel.caller_npanxx, '415555')
        self.assertEqual(channel.caller_reputation_id.e164, TRUSTED_NUMBER)
        self.assertEqual(channel.caller_reputation_id.attest_a_count, 1)
        self.assertEqual(call.caller_verdict, 'trusted')
        self.assertEqual(call.caller_signal_ids.mapped('code'), ['trusted_known'])
        self.assertEqual(call.caller_commercial_partner_id, self.partner.commercial_partner_id)

    def test_failed_validation_is_likely_spoof_and_alerts(self):
        """A failed StirVerstat raises attest_failed, a spoof verdict and a bus alert to the ringing user."""
        user = new_test_user(self.env, login='caller_intel_ring_user')
        channel = self._channel(SPOOF_NUMBER, called_user=user.id)
        Bus = type(self.env['bus.bus'])
        with patch.object(Bus, '_sendone') as sendone:
            channel._caller_intel_ingest({'attestation': 'failed'})
        sendone.assert_called_once_with('connect_actions_%s' % user.id, 'connect_spoof_alert', ANY)
        signals = self.env['connect.caller.signal'].search([('channel_id', '=', channel.id)])
        self.assertIn('attest_failed', signals.mapped('code'))
        call = self._ring(SPOOF_NUMBER, StirVerstat='TN-Validation-Failed-A')
        self.assertEqual(call.channels.caller_attestation, 'failed')
        self.assertEqual(call.caller_verdict, 'likely_spoof')
        self.assertEqual(call.caller_risk_score, 1.0)

    def test_ingest_failure_does_not_break_the_event(self):
        """An exception inside the heuristics leaves the event processed and the call unflagged."""
        with patch('odoo.addons.connect_caller_intel.models.channel.heuristics.evaluate',
                   side_effect=RuntimeError('boom')), self.assertLogs(
                'odoo.addons.connect_caller_intel.models.channel', level='WARNING') as logs:
            call = self._ring(SPOOF_NUMBER, StirVerstat='TN-Validation-Passed-A')
        self.assertTrue(call)
        self.assertTrue(call.channels)
        self.assertFalse(call.channels.caller_attestation)
        self.assertEqual(call.caller_verdict, 'unknown')
        self.assertTrue(any('ingest failed' in line for line in logs.output))

    def test_ingest_query_budget(self):
        """Ingest for a returning caller stays within ten queries once caches are warm."""
        self.env['connect.caller.reputation']._get_or_create(WARM_NUMBER)
        self._channel(WARM_NUMBER)._caller_intel_ingest({'attestation': 'A'})
        channel = self._channel(WARM_NUMBER)
        self.env.flush_all()
        with self.assertQueryCount(10):
            channel._caller_intel_ingest({'attestation': 'A'})

    def test_missed_call_notice_names_reporters_and_escapes(self):
        """The missed-call notice lists who flagged the caller, with third-party text escaped."""
        rep = self.env['connect.caller.reputation']._get_or_create(SPAM_NUMBER)
        source = self.env.ref('connect_caller_intel.source_ftc_dnc')
        self.env['connect.caller.mark'].create([
            {'reputation_id': rep.id, 'source_id': source.id, 'reporter': 'FTC Do Not Call complaints',
             'external_ref': 'a', 'report_count': 12},
            {'reputation_id': rep.id, 'source_id': source.id, 'reporter': 'Evil <script>alert(1)</script>',
             'external_ref': 'b', 'report_count': 1},
        ])
        call = self._ring(SPAM_NUMBER, StirVerstat='TN-Validation-Passed-C')
        self.assertEqual(call.caller_verdict, 'likely_spam')
        call.register_call_post_message(
            call, subtype_xmlid='mail.mt_comment', subject='Missed', body='No-answer incoming call.')
        body = str(call.message_ids[:1].body)
        self.assertIn('FTC Do Not Call complaints', body)
        self.assertIn('Evil', body)
        self.assertNotIn('<script>', body)
        self.assertIn('No-answer incoming call.', body)

    def test_trusted_caller_notice_is_unchanged(self):
        """A trusted caller's missed-call notice carries no caller check line."""
        call = self._ring(TRUSTED_NUMBER, partner=self.partner, StirVerstat='TN-Validation-Passed-A')
        call.register_call_post_message(
            call, subtype_xmlid='mail.mt_comment', subject='Missed', body='No-answer incoming call.')
        self.assertNotIn('Caller check', str(call.message_ids[:1].body))

    def test_staff_report_buttons(self):
        """Mark spam adds a staff mark naming the user; not spam clears the active marks."""
        call = self._ring(SPAM_NUMBER, StirVerstat='TN-Validation-Passed-B')
        call.action_caller_mark_spam()
        marks = call.caller_reputation_id.mark_ids
        self.assertEqual(marks.source_id.code, 'staff')
        self.assertIn(self.env.user.name, marks.reporter)
        call.action_caller_mark_trusted()
        self.assertEqual(marks.state, 'cleared')
        self.assertEqual(marks.cleared_by, self.env.user)
