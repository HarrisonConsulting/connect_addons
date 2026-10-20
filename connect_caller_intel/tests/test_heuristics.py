from odoo.tests import TransactionCase, tagged

from ..services import heuristics, numbering, stir

OWN = ["+13125550142"]
H_A = {"A": 5}

# (code, positive facts, negative facts)
CASES = [
    ("attest_failed", {"attestation": "failed"}, {"attestation": "A"}),
    ("partner_unattested", {"partner_names": ["Acme"], "attestation": "none", "history": H_A},
     {"partner_names": ["Acme"], "attestation": "none", "history": {"A": 2}}),
    ("partner_attest_drop", {"partner_names": ["Acme"], "attestation": "B", "history": H_A},
     {"partner_names": ["Acme"], "attestation": "A", "history": H_A}),
    ("cnam_mismatch", {"partner_names": ["Acme Corp"], "cnam": "BOB PLUMBING"},
     {"partner_names": ["Acme Corp"], "cnam": "WIRELESS CALLER"}),
    ("own_did_as_caller", {"e164": OWN[0], "own_dids": OWN}, {"e164": "+13125559999", "own_dids": OWN}),
    ("neighbor_spoof", {"npanxx": "312555", "own_dids": OWN, "e164": "+13125559999", "attestation": "C"},
     {"npanxx": "312555", "own_dids": OWN, "e164": "+13125559999", "attestation": "A"}),
    ("invalid_number", {"numbering": {"valid": False}}, {"numbering": {"valid": True}}),
    ("anonymous", {"anonymous": True}, {"anonymous": False}),
    ("burst_same_number", {"burst_count": 3}, {"burst_count": 2}),
    ("snowshoe_npanxx", {"snowshoe_count": 4}, {"snowshoe_count": 3}),
    ("wangiri_risk", {"missed_zero_duration": True, "numbering": {"risky_country": True}},
     {"missed_zero_duration": False, "numbering": {"risky_country": True}}),
    ("toll_free_unattested", {"numbering": {"line_type": "toll_free"}, "attestation": "C"},
     {"numbering": {"line_type": "toll_free"}, "attestation": "A"}),
    ("reported_spam", {"marks": [{"source": "ftc_dnc", "report_count": 6}]},
     {"marks": [{"source": "ftc_dnc", "report_count": 1}], "spam_score": 0.1}),
    ("staff_marked", {"marks": [{"source": "staff", "report_count": 1}]},
     {"marks": [{"source": "ftc_dnc", "report_count": 1}]}),
    ("trusted_known", {"partner_names": ["Acme"], "attestation": "A"},
     {"partner_names": ["Acme"], "attestation": "A", "marks": [{"source": "staff"}]}),
]


@tagged('post_install', '-at_install')
class TestHeuristics(TransactionCase):

    def _codes(self, facts):
        return {s["code"] for s in heuristics.evaluate(facts)}

    def test_each_code_positive_and_negative(self):
        """Every heuristic code fires on its positive case and stays silent on its negative."""
        for code, pos, neg in CASES:
            with self.subTest(code=code):
                self.assertIn(code, self._codes(pos))
                self.assertNotIn(code, self._codes(neg))

    def test_signal_shape(self):
        """Signals are dicts with code, severity and reason."""
        for s in heuristics.evaluate({"attestation": "failed"}):
            self.assertTrue(s["reason"])
            self.assertIn(s["severity"], ("info", "warn", "high"))

    def test_verdict(self):
        """Spoof outranks spam; partner match without signals is known."""
        sigs = heuristics.evaluate({"attestation": "failed", "marks": [{"source": "staff"}]})
        self.assertEqual(heuristics.verdict(sigs), "likely_spoof")
        self.assertEqual(heuristics.verdict(heuristics.evaluate({"marks": [{"source": "staff"}]})), "likely_spam")
        self.assertEqual(heuristics.verdict([], partner_match=True), "known")
        self.assertEqual(heuristics.verdict([]), "unknown")

    def test_stir(self):
        """StirVerstat values map to attestation and verified flag."""
        self.assertEqual(stir.parse_stir_verstat("TN-Validation-Passed-B")["attestation"], "B")
        self.assertTrue(stir.parse_stir_verstat("TN-Validation-Passed-A")["verified"])
        self.assertEqual(stir.parse_stir_verstat("TN-Validation-Failed-C")["attestation"], "failed")
        self.assertEqual(stir.parse_stir_verstat("No-TN-Validation")["attestation"], "none")
        self.assertEqual(stir.parse_stir_verstat(None)["attestation"], "none")
        self.assertTrue(stir.is_anonymous_caller("anonymous"))
        self.assertFalse(stir.is_anonymous_caller("+13125550142"))

    def test_numbering(self):
        """Offline numbering facts classify validity, type and reserved ranges."""
        valid = numbering.analyze("+12025550123")
        self.assertEqual(valid["npanxx"], "202555")
        self.assertTrue(valid["reserved_555"])
        self.assertEqual(numbering.analyze("+18005551234")["line_type"], "toll_free")
        self.assertTrue(numbering.analyze("+12115551234")["n11"])
        self.assertTrue(numbering.analyze("+12020551234")["bad_nxx"])
        self.assertEqual(numbering.analyze("garbage")["e164"], "")
