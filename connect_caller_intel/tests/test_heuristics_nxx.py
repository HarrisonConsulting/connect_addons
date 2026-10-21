from odoo.tests import TransactionCase, tagged

from ..services import heuristics

AVAILABLE = {"status": "available", "in_service": False, "stale": False}


def _codes(facts):
    return {s["code"]: s["severity"] for s in heuristics.evaluate(facts)}


@tagged('post_install', '-at_install')
class TestHeuristicsNxx(TransactionCase):

    def test_available_exchange_is_high(self):
        """An available exchange raises nxx_available at high severity."""
        self.assertEqual(_codes({"nxx": AVAILABLE, "attestation": "none"}).get("nxx_available"), "high")

    def test_assigned_in_service_is_silent(self):
        """An assigned exchange in service raises neither exchange signal."""
        codes = _codes({"nxx": {"status": "assigned", "in_service": True, "stale": False}})
        self.assertNotIn("nxx_available", codes)
        self.assertNotIn("nxx_not_in_service", codes)

    def test_not_in_service_cases(self):
        """Unavailable, unlisted and assigned-but-out-of-service exchanges warn."""
        for status, in_service in (("unavailable", False), ("unlisted", False), ("assigned", False)):
            with self.subTest(status=status):
                codes = _codes({"nxx": {"status": status, "in_service": in_service, "stale": False}})
                self.assertEqual(codes.get("nxx_not_in_service"), "warn")
                self.assertNotIn("nxx_available", codes)

    def test_attestation_a_suppresses_both(self):
        """A carrier-attested A call raises neither exchange signal."""
        for nxx in (AVAILABLE, {"status": "unlisted", "in_service": False, "stale": False}):
            codes = _codes({"nxx": nxx, "attestation": "A"})
            self.assertNotIn("nxx_available", codes)
            self.assertNotIn("nxx_not_in_service", codes)

    def test_stale_downgrades_and_is_not_spoof(self):
        """A stale load downgrades nxx_available to warn, and a warn gives no likely_spoof."""
        stale = dict(AVAILABLE, stale=True)
        signals = heuristics.evaluate({"nxx": stale})
        self.assertEqual({s["code"]: s["severity"] for s in signals}["nxx_available"], "warn")
        self.assertNotEqual(heuristics.verdict(signals), "likely_spoof")
        self.assertEqual(heuristics.verdict(heuristics.evaluate({"nxx": AVAILABLE})), "likely_spoof")

    def test_no_nxx_facts_no_signals(self):
        """Facts without nxx, and with an empty nxx, raise neither signal."""
        for facts in ({}, {"nxx": {}}):
            codes = _codes(facts)
            self.assertNotIn("nxx_available", codes)
            self.assertNotIn("nxx_not_in_service", codes)

    def test_source_weights(self):
        """Known sources weigh as declared and an unknown source weighs fully."""
        self.assertEqual(heuristics.source_weight("web_reports"), 0.6)
        self.assertEqual(heuristics.source_weight("ipqs"), 0.5)
        self.assertEqual(heuristics.source_weight("anything_else"), 1.0)
