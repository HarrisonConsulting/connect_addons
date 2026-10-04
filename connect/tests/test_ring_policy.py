"""Pure evaluator tests; these do not establish deployed routing or live evidence."""

from dataclasses import FrozenInstanceError, replace
import unittest

from ..services import ring_policy as policy


class TestRingPolicy(unittest.TestCase):
    # Precedent: odoo/tests/loader.py discovers unittest.TestCase; the
    # tag_selector accepts non-BaseCase tests with explicit metadata.
    test_tags = {'standard', 'post_install'}
    test_module = 'connect'

    def setUp(self):
        self.recipient = policy.RecipientScope(1, 2, 3, 1)
        self.twilio = policy.AccountBinding('twilio', 10, 1)
        self.voicetel = policy.AccountBinding('voicetel', 20, 2)
        self.snapshot = policy.RingPolicySnapshot(
            revision='test-revision', recipient=self.recipient,
            timezone='America/Indiana/Indianapolis',
            bindings=(self.twilio, self.voicetel),
            intervals=tuple(policy.WeeklyInterval(day, 300, 1200) for day in range(5)),
            closed_dates=(), maximum_clock_uncertainty_seconds=1,
            maximum_event_age_seconds=30, business_tone='business-local')

    def context(self, when='2026-10-05T12:00:00Z', **changes):
        context = policy.EvaluationContext(
            'test-event', 'test-call', self.recipient, self.twilio, when, when,
            clock_uncertainty_seconds=0, ingress_verified=True, ingress_policy_bound=True)
        return replace(context, **changes)

    def decision(self, when='2026-10-05T12:00:00Z', **changes):
        return policy.evaluate_ring_policy(self.snapshot, self.context(when, **changes))

    def test_opening_boundary(self):
        for when, expected in (('2026-10-05T08:59:59Z', 'deny'),
                               ('2026-10-05T09:00:00Z', 'allow'),
                               ('2026-10-05T09:00:01Z', 'allow')):
            with self.subTest(when=when):
                self.assertEqual(self.decision(when).admission, expected)

    def test_closing_boundary(self):
        for when, expected in (('2026-10-05T23:59:59Z', 'allow'),
                               ('2026-10-06T00:00:00Z', 'deny'),
                               ('2026-10-06T00:00:01Z', 'deny')):
            with self.subTest(when=when):
                self.assertEqual(self.decision(when).admission, expected)

    def test_weekends(self):
        for when in ('2026-10-03T16:00:00Z', '2026-10-04T16:00:00Z'):
            self.assertEqual(self.decision(when).admission, 'deny')

    def test_spring_dst_opening_shift(self):
        for when, expected in (('2026-03-06T09:59:59Z', 'deny'),
                               ('2026-03-06T10:00:00Z', 'allow'),
                               ('2026-03-09T08:59:59Z', 'deny'),
                               ('2026-03-09T09:00:00Z', 'allow')):
            self.assertEqual(self.decision(when).admission, expected)

    def test_spring_transition_uses_real_utc_instants(self):
        for when in ('2026-03-08T06:59:59Z', '2026-03-08T07:00:00Z'):
            self.assertEqual(self.decision(when).admission, 'deny')

    def test_fall_dst_opening_shift(self):
        self.assertEqual(self.decision('2026-10-30T09:00:00Z').admission, 'allow')
        self.assertEqual(self.decision('2026-11-02T09:59:59Z').admission, 'deny')
        self.assertEqual(self.decision('2026-11-02T10:00:00Z').admission, 'allow')

    def test_fall_repeated_hour(self):
        for when in ('2026-11-01T05:30:00Z', '2026-11-01T06:30:00Z'):
            self.assertEqual(self.decision(when).admission, 'deny')

    def test_no_implicit_holidays(self):
        self.assertEqual(self.decision('2026-12-25T15:00:00Z').admission, 'allow')

    def test_explicit_closure(self):
        snapshot = replace(self.snapshot, closed_dates=('2026-12-25',))
        result = policy.evaluate_ring_policy(snapshot, self.context('2026-12-25T15:00:00Z'))
        self.assertEqual(result.admission, 'deny')

    def test_empty_intervals_explicitly_close(self):
        result = policy.evaluate_ring_policy(replace(self.snapshot, intervals=()), self.context())
        self.assertEqual(result.admission, 'deny')

    def test_second_carrier_same_schedule(self):
        self.assertEqual(self.decision(binding=self.voicetel).admission, 'allow')

    def test_wrong_provider_tool_slot(self):
        for binding in (policy.AccountBinding('unknown', 10, 1),
                        policy.AccountBinding('twilio', 20, 1),
                        policy.AccountBinding('twilio', 10, 2), None, {}):
            self.assertEqual(self.decision(binding=binding).admission, 'deny')

    def test_wrong_company_person_endpoint_epoch(self):
        for changes in ({'company_id': 4}, {'person_id': 4},
                        {'endpoint_id': 4}, {'configuration_epoch': 2}):
            recipient = replace(self.recipient, **changes)
            self.assertEqual(self.decision(recipient=recipient).admission, 'deny')
        self.assertEqual(self.decision(recipient=None).admission, 'deny')

    def test_scope_cannot_accept_bool_or_empty_provider(self):
        for call in (lambda: policy.RecipientScope(True, 2, 3, 1),
                     lambda: policy.AccountBinding('', 10, 1),
                     lambda: policy.AccountBinding('twilio', 10, True)):
            with self.assertRaises(ValueError):
                call()

    def test_unserializable_integer_magnitude_rejected(self):
        excessive = 10 ** 5000
        for call in (lambda: replace(self.snapshot, maximum_event_age_seconds=excessive),
                     lambda: replace(self.recipient, person_id=excessive),
                     lambda: replace(self.twilio, tool_id=excessive)):
            with self.assertRaises(ValueError):
                call()

    def test_missing_ingress_flags_unknown(self):
        context = policy.EvaluationContext('e', 'c', self.recipient, self.twilio,
                                          '2026-10-05T12:00:00Z', '2026-10-05T12:00:00Z', 0)
        self.assertEqual(policy.evaluate_ring_policy(self.snapshot, context).admission, 'unknown')

    def test_unverified_or_unbound_ingress_unknown(self):
        for changes in ({'ingress_verified': False}, {'ingress_policy_bound': False},
                        {'ingress_verified': 1}, {'ingress_policy_bound': 'true'}):
            self.assertEqual(self.decision(**changes).admission, 'unknown')

    def test_missing_policy_or_context_unknown(self):
        self.assertEqual(policy.evaluate_ring_policy(None, self.context()).admission, 'unknown')
        self.assertEqual(policy.evaluate_ring_policy(self.snapshot, {}).admission, 'unknown')

    def test_missing_clock_uncertainty_unknown(self):
        self.assertEqual(self.decision(clock_uncertainty_seconds=None).admission, 'unknown')

    def test_invalid_clock_values_unknown(self):
        for when in (None, [], {}, 0, True, '', 'bad',
                     '2026-10-05T12:00:00', '2026-10-05T12:00:00-04:00'):
            self.assertEqual(self.decision(when).admission, 'unknown')

    def test_non_finite_or_excess_uncertainty_unknown(self):
        for uncertainty in (float('nan'), float('inf'), -1, True, 2, '0', 10 ** 500):
            self.assertEqual(self.decision(clock_uncertainty_seconds=uncertainty).admission, 'unknown')

    def test_stale_and_future_events_unknown(self):
        for observed in ('2026-10-05T11:59:00Z', '2026-10-05T12:00:01Z'):
            self.assertEqual(self.decision(observed_at=observed).admission, 'unknown')

    def test_event_age_boundary(self):
        self.assertEqual(self.decision(observed_at='2026-10-05T11:59:30Z').admission, 'allow')
        self.assertEqual(self.decision(observed_at='2026-10-05T11:59:29.999999Z').admission, 'unknown')

    def test_uncertainty_crossing_open_or_close_unknown(self):
        for when in ('2026-10-05T09:00:00Z', '2026-10-06T00:00:00Z'):
            self.assertEqual(self.decision(when, clock_uncertainty_seconds=0.5).admission, 'unknown')

    def test_intermediate_closed_gap_not_hidden_by_endpoint_samples(self):
        snapshot = replace(self.snapshot, maximum_clock_uncertainty_seconds=60,
                           intervals=(policy.WeeklyInterval(0, 300, 301),
                                      policy.WeeklyInterval(0, 302, 304)))
        result = policy.evaluate_ring_policy(snapshot, self.context(
            '2026-10-05T09:02:15Z', clock_uncertainty_seconds=60))
        self.assertEqual(result.admission, 'unknown')

    def test_datetime_extremes_unknown(self):
        for when in ('0001-01-01T00:00:00Z', '9999-12-31T23:59:59.999999Z'):
            self.assertEqual(self.decision(when, clock_uncertainty_seconds=1).admission, 'unknown')

    def test_no_after_hours_exception_surface(self):
        result = self.decision('2026-10-05T00:30:00Z')
        self.assertEqual((result.admission, result.reason), ('deny', 'after-hours-closed'))
        for claim in ('caller_id', 'urgent_keypress', 'alert_info', 'verified_exception'):
            with self.assertRaises(TypeError):
                self.context(**{claim: 'untrusted-priority'})

    def test_no_auto_answer_or_unmeasured_tone_claim(self):
        for when in ('2026-10-05T12:00:00Z', '2026-10-05T00:30:00Z'):
            result = self.decision(when)
            self.assertFalse(result.auto_answer)
            self.assertEqual(result.requested_local_tone,
                             'business-local' if result.admission == 'allow' else None)

    def test_empty_event_and_call_ids_denied(self):
        for changes in ({'event_id': ''}, {'call_id': ''}, {'call_id': []}, {'event_id': ' '}):
            self.assertEqual(self.decision(**changes).admission, 'deny')

    def test_snapshots_and_values_frozen(self):
        for value, name in ((self.snapshot, 'revision'), (self.recipient, 'person_id'),
                            (self.twilio, 'provider'), (self.snapshot.intervals[0], 'weekday')):
            with self.assertRaises(FrozenInstanceError):
                setattr(value, name, 'changed')

    def test_mutable_snapshot_inputs_rejected(self):
        for changes in ({'bindings': [self.twilio]}, {'intervals': []}, {'closed_dates': []}):
            with self.assertRaises(ValueError):
                replace(self.snapshot, **changes)

    def test_duplicate_bindings_rejected(self):
        for bindings in ((self.twilio, policy.AccountBinding('voicetel', 20, 1)),
                         (self.twilio, policy.AccountBinding('twilio', 10, 2))):
            with self.assertRaises(ValueError):
                replace(self.snapshot, bindings=bindings)

    def test_invalid_interval_rejected(self):
        for args in ((True, 300, 1200), (7, 300, 1200), (0, 1200, 300),
                     (0, 300, 300), (0, 300.5, 1200), (0, -1, 1200)):
            with self.assertRaises(ValueError):
                policy.WeeklyInterval(*args)

    def test_invalid_snapshot_limits_and_tones_rejected(self):
        for changes in ({'maximum_event_age_seconds': float('nan')},
                        {'maximum_clock_uncertainty_seconds': 61},
                        {'maximum_event_age_seconds': '30'},
                        {'business_tone': 'https://untrusted.example/tone'},
                        {'business_tone': 'priority;answer-after=0'},
                        {'closed_dates': ('20261225',)}, {'revision': ''}):
            with self.assertRaises(ValueError):
                replace(self.snapshot, **changes)

    def test_digest_binds_actual_policy_and_scope(self):
        baseline = self.snapshot.digest()
        for changes in ({'revision': 'other'}, {'business_tone': 'other'},
                        {'recipient': replace(self.recipient, configuration_epoch=2)},
                        {'closed_dates': ('2026-12-25',)}):
            self.assertNotEqual(baseline, replace(self.snapshot, **changes).digest())
        self.assertEqual(baseline, self.decision().policy_sha256)
