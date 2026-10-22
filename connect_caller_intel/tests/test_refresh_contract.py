from datetime import timedelta
from unittest import mock

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

from odoo.addons.queue_job.exception import RetryableJobError

from ..services.adapters import registry
from ..services.adapters.base import Adapter, AdapterThrottled


class FakeAdapter(Adapter):
    code = 'fake_nanp'
    nanp_only = True
    result = {'found': False, 'marks': []}
    error = None

    def lookup(self, env, e164):
        if self.error:
            raise self.error
        return self.result


@tagged('post_install', '-at_install')
class TestRefreshContract(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Rep = cls.env['connect.caller.reputation']
        cls.Source = cls.env['connect.caller.source']
        cls.adapter = FakeAdapter()
        cls.source = cls.Source.create({
            'code': 'fake_nanp', 'name': 'Fake', 'tier': 'async', 'ttl_hours': 24,
            'attribution': 'Fake credit'})
        cls.source.write({'enabled': True})
        cls.rep = cls.Rep.create({'e164': '+14155550150'})
        cls.uk = cls.Rep.create({'e164': '+442071838750'})

    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(registry.ADAPTERS, {'fake_nanp': self.adapter, 'gated': self.adapter})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.adapter.result = {'found': False, 'marks': []}
        self.adapter.error = None

    def _identity(self, ref, name, confidence):
        return {'name': name, 'kind': 'business', 'external_ref': ref, 'confidence': confidence}

    def _enqueued_codes(self, rep):
        with mock.patch.object(type(self.Rep), 'with_delay') as delay:
            rep._enqueue_stale_sources()
        return {call.kwargs['identity_key'].rsplit(':', 1)[1]
                for call in delay.call_args_list if call.kwargs}

    def test_nanp_only_adapter_skips_foreign_numbers(self):
        """Every nanp_only adapter is neither enqueued nor run for a +44 number."""
        nanp_only = {code for code, adapter in registry.ADAPTERS.items() if adapter.nanp_only}
        self.assertLessEqual({'fake_nanp', 'fcc_complaints'}, nanp_only)
        self.assertFalse(self._enqueued_codes(self.uk) & nanp_only)
        for code in nanp_only:
            self.assertEqual(self.uk._refresh_source(code), 'skipped', code)
        self.assertIn('fake_nanp', self._enqueued_codes(self.rep))

    def test_cost_classes_narrow_the_sources(self):
        """Asking for free sources only leaves a freemium source out."""
        self.source.cost_class = 'freemium'
        with mute_logger('odoo.addons.connect_caller_intel.services.adapters.base'):
            self._assert_cost_narrowing()

    def _assert_cost_narrowing(self):
        self.assertNotIn(self.source, self.Rep._usable_async_sources(('free',)))
        self.assertIn(self.source, self.Rep._usable_async_sources(('freemium',)))
        self.assertIn(self.source, self.Rep._usable_async_sources())

    def test_throttle_retries_without_an_outcome(self):
        """AdapterThrottled becomes a retry that keeps the counter and records nothing."""
        self.adapter.error = AdapterThrottled('paced', seconds=3)
        with self.assertRaises(RetryableJobError) as ctx:
            self.rep._refresh_source('fake_nanp')
        self.assertEqual(ctx.exception.seconds, 3)
        self.assertTrue(ctx.exception.ignore_retry)
        self.assertNotIn('fake_nanp', self.rep.source_checked_json or {})

    def test_identities_upsert_follow_top_confidence_and_clear(self):
        """Identities upsert by external_ref, drive the candidate name, and go on found=False."""
        Identity = self.env['connect.caller.identity']
        self.adapter.result = {'found': True, 'marks': [], 'identities': [
            self._identity('Q1', 'Low Co', 0.3), self._identity('Q2', 'High Co', 0.7)]}
        self.rep._refresh_source('fake_nanp')
        self.assertEqual(len(self.rep.identity_ids), 2)
        self.assertEqual(self.rep.candidate_business_name, 'High Co')
        self.assertEqual(set(self.rep.identity_ids.mapped('attribution')), {'Fake credit'})
        self.adapter.result = {'found': True, 'marks': [], 'identities': [
            self._identity('Q1', 'Renamed Co', 0.9)]}
        self.rep._refresh_source('fake_nanp')
        self.assertEqual(len(self.rep.identity_ids), 2)
        self.assertEqual(self.rep.candidate_business_name, 'Renamed Co')
        self.adapter.result = {'found': False, 'marks': []}
        self.rep._refresh_source('fake_nanp')
        self.assertFalse(Identity.search([('reputation_id', '=', self.rep.id)]))

    def test_expired_identity_is_purged(self):
        """The purge cron drops identities whose expiry has passed and keeps the rest."""
        Identity = self.env['connect.caller.identity']
        now = fields.Datetime.now()
        base = {'reputation_id': self.rep.id, 'source_id': self.source.id, 'name': 'X',
                'attribution': 'Fake credit'}
        old = Identity.create(dict(base, external_ref='old', expires_at=now - timedelta(hours=1)))
        keep = Identity.create(dict(base, external_ref='keep', expires_at=now + timedelta(hours=1)))
        self.Rep._cron_expire_and_purge()
        self.assertFalse(old.exists())
        self.assertTrue(keep.exists())

    def test_numbering_allowlist(self):
        """Only allowlisted numbering keys reach the reputation."""
        self.adapter.result = {'found': True, 'marks': [], 'numbering': {
            'rate_center': 'RIDGELAND', 'numbering_state': 'MS', 'e164': '+19999999999'}}
        self.rep._refresh_source('fake_nanp')
        self.assertEqual(self.rep.rate_center, 'RIDGELAND')
        self.assertEqual(self.rep.numbering_state, 'MS')
        self.assertEqual(self.rep.e164, '+14155550150')

    def test_purpose_acknowledgement(self):
        """A purpose-gated source needs action_enable_with_purpose, which records both acknowledgements."""
        gated = self.Source.create({
            'code': 'gated', 'name': 'Gated', 'tier': 'async', 'cost_class': 'paid',
            'requires_purpose_ack': True})
        with self.assertRaises(UserError):
            gated.action_enable()
        self.assertFalse(gated.enabled)
        gated.action_enable_with_purpose()
        self.assertTrue(gated.enabled)
        self.assertEqual(gated.cost_acknowledged_by, self.env.user)
        self.assertEqual(gated.purpose_acknowledged_by, self.env.user)
        self.assertTrue(gated.purpose_acknowledged_at)
        gated.action_disable()
        self.assertFalse(gated.purpose_acknowledged_by)

    def test_throttle_raises_inside_the_interval(self):
        """A second request inside the interval raises AdapterThrottled."""
        self.Source._caller_intel_throttle('fake_nanp', 1.0)
        with self.assertRaises(AdapterThrottled) as ctx:
            self.Source._caller_intel_throttle('fake_nanp', 1.0)
        self.assertGreaterEqual(ctx.exception.seconds, 1)

    def test_web_reports_mark_is_weighted(self):
        """A web_reports mark with ten reports scores 0.6, a staff one scores 1.0."""
        for code, expected in (('web_reports', 0.6), ('staff', 1.0)):
            source = self.Source.search([('code', '=', code)], limit=1) or self.Source.create(
                {'code': code, 'name': code, 'tier': 'async'})
            rep = self.Rep.create({'e164': '+1415555%04d' % len(code)})
            self.env['connect.caller.mark'].create({
                'reputation_id': rep.id, 'source_id': source.id, 'reporter': 'R',
                'report_count': 10, 'external_ref': code})
            self.assertAlmostEqual(rep.spam_score, expected)

    def test_card_carries_calling_from_identities_and_steps(self):
        """The ring card returns calling_from, the unexpired identities and clearing steps."""
        rep = self.Rep.create({'e164': '+14155550151', 'rate_center': 'RIDGELAND',
                               'numbering_state': 'MS', 'ocn_company': 'Acme Telco'})
        now = fields.Datetime.now()
        Identity = self.env['connect.caller.identity']
        Identity.create({'reputation_id': rep.id, 'source_id': self.source.id, 'name': 'Shown',
                         'attribution': 'Fake credit', 'external_ref': 'a', 'confidence': 0.5,
                         'url': 'javascript:alert(1)', 'expires_at': now + timedelta(hours=1)})
        Identity.create({'reputation_id': rep.id, 'source_id': self.source.id, 'name': 'Gone',
                         'attribution': 'Fake credit', 'external_ref': 'b', 'confidence': 0.9,
                         'expires_at': now - timedelta(hours=1)})
        self.env['connect.caller.remediation'].create({
            'code': 'fake_rem', 'name': 'Fake rem', 'steps': 'Call the board.',
            'match_reporters': 'Fake Board'})
        self.env['connect.caller.mark'].create({
            'reputation_id': rep.id, 'source_id': self.source.id, 'reporter': 'fake board',
            'external_ref': 'm'})
        card = self.env['connect.call'].caller_intel_card('+14155550151')
        self.assertEqual(card['calling_from'], 'Ridgeland, MS · Acme Telco')
        self.assertEqual([i['name'] for i in card['identities']], ['Shown'])
        self.assertEqual(card['identities'][0]['url'], '')
        self.assertEqual(card['marks'][0]['clearing_steps'], 'Call the board.')
