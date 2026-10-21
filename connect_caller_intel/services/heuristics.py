"""Spoof and adversarial caller heuristics: pure functions over plain facts."""

DEFAULTS = {
    "burst_calls": 3,
    "snowshoe_numbers": 4,
    "spam_score": 0.5,
    "mark_reports": 5,
    "partner_a_history": 3,
    "nxx_stale_days": 45,
}
SOURCE_WEIGHTS = {"staff": 1.0, "ftc_dnc": 1.0, "fcc_complaints": 1.0, "web_reports": 0.6, "ipqs": 0.5}
IDENTITY_CONFIDENCE = {"wikidata": 0.3, "osm": 0.3, "google_places": 0.7, "trestle": 0.5}
_RANK = {"A": 3, "B": 2, "C": 1, "none": 0, "failed": -1}
GENERIC_CNAM = frozenset({"wireless caller", "unknown", "unavailable", "toll free", "caller", "private", "anonymous"})
HIGH_SPOOF = ("attest_failed", "partner_unattested", "own_did_as_caller", "invalid_number", "nxx_available")


def source_weight(code):
    """How far one source's reports count toward the spam score; unknown sources count fully."""
    return SOURCE_WEIGHTS.get(code, 1.0)


def _sig(code, severity, reason, source=None):
    return {"code": code, "severity": severity, "reason": reason, "source": source}


def _tokens(text):
    return {t for t in "".join(c.lower() if c.isalnum() else " " for c in text or "").split() if len(t) > 1}


def _mode(history):
    """Most frequent attestation in history; ties resolve to the higher level."""
    counts = {k: history.get(k, 0) for k in ("A", "B", "C", "none")}
    if not any(counts.values()):
        return None
    return max(counts, key=lambda k: (counts[k], _RANK[k]))


def evaluate(facts, thresholds=None):
    """Return the list of signal dicts {code, severity, reason, source} for one inbound call."""
    t = dict(DEFAULTS, **(thresholds or {}))
    out = []
    att = facts.get("attestation", "none")
    partner = bool(facts.get("partner_names") or facts.get("partner_match"))
    history = facts.get("history") or {}
    marks = [m for m in facts.get("marks") or [] if m.get("active", True)]
    numbering = facts.get("numbering") or {}
    e164 = facts.get("e164") or ""
    low_att = att in ("C", "none")

    if facts.get("anonymous"):
        out.append(_sig("anonymous", "warn", "Caller identity withheld", "stir_shaken"))
    if att == "failed":
        out.append(_sig("attest_failed", "high", "Carrier signature validation failed", "stir_shaken"))
    if partner and low_att and history.get("A", 0) >= t["partner_a_history"] and not history.get("C", 0):
        out.append(_sig("partner_unattested", "high", "Known contact normally attested A now arrives unattested", "stir_shaken"))
    elif partner and att in _RANK and _mode(history) and _RANK[att] < _RANK[_mode(history)] and att != "failed":
        out.append(_sig("partner_attest_drop", "warn", "Attestation lower than this contact's usual level", "stir_shaken"))
    cnam = (facts.get("cnam") or "").strip()
    names = facts.get("partner_names") or []
    if cnam and names and cnam.lower() not in GENERIC_CNAM and not any(_tokens(cnam) & _tokens(n) for n in names):
        out.append(_sig("cnam_mismatch", "warn", "Presented caller name does not match the contact", "stir_shaken"))
    if e164 and e164 in set(facts.get("own_dids") or []):
        out.append(_sig("own_did_as_caller", "high", "Caller presents one of our own numbers", "numbering_plan"))
    npanxx = facts.get("npanxx") or numbering.get("npanxx") or ""
    own_npanxx = {d[2:8] for d in facts.get("own_dids") or [] if d.startswith("+1") and len(d) == 12}
    if npanxx and npanxx in own_npanxx and not partner and att != "A" and e164 not in set(facts.get("own_dids") or []):
        out.append(_sig("neighbor_spoof", "high" if marks else "warn", "Caller shares our number's area code and exchange", "numbering_plan"))
    if numbering and (not numbering.get("valid", True) or numbering.get("n11") or numbering.get("bad_nxx") or numbering.get("reserved_555")):
        out.append(_sig("invalid_number", "high", "Number is not a valid assigned pattern", "numbering_plan"))
    nxx = facts.get("nxx") or {}
    if nxx and att != "A":
        status = nxx.get("status")
        if status == "available":
            out.append(_sig("nxx_available", "warn" if nxx.get("stale") else "high",
                            "Number sits in an exchange nobody has been assigned", "nanpa_co_codes"))
        elif status in ("unavailable", "unlisted") or (status == "assigned" and not nxx.get("in_service")):
            out.append(_sig("nxx_not_in_service", "warn", "Number sits in an exchange not in service", "nanpa_co_codes"))
    if facts.get("burst_count", 0) >= t["burst_calls"]:
        out.append(_sig("burst_same_number", "warn", "Repeated calls from this number within 10 minutes", "local_partner"))
    if not partner and facts.get("snowshoe_count", 0) >= t["snowshoe_numbers"]:
        out.append(_sig("snowshoe_npanxx", "warn", "Many unknown numbers from one exchange within 10 minutes", "numbering_plan"))
    if facts.get("missed_zero_duration") and (numbering.get("risky_country") or facts.get("risky_country")):
        out.append(_sig("wangiri_risk", "high", "Missed call from a high-risk international prefix; do not call back", "numbering_plan"))
    if numbering.get("line_type") == "toll_free" and low_att:
        out.append(_sig("toll_free_unattested", "info", "Toll-free caller without full attestation", "stir_shaken"))
    strongest = max(marks, key=lambda m: m.get("report_count", 0), default=None)
    if marks and (facts.get("spam_score", 0) >= t["spam_score"] or (strongest and strongest.get("report_count", 0) >= t["mark_reports"])):
        out.append(_sig("reported_spam", "warn", "Reported as spam by other systems", strongest.get("source")))
    if any(m.get("source") == "staff" for m in marks):
        out.append(_sig("staff_marked", "high", "Marked as spam by staff", "staff"))
    if partner and att == "A" and not marks:
        out.append(_sig("trusted_known", "info", "Known contact with full attestation", "local_partner"))
    return out


def verdict(signals, partner_match=False):
    """Highest severity wins; spoof outranks spam; else known or unknown."""
    codes = {s["code"] for s in signals}
    if {s["code"] for s in signals if s["severity"] == "high"} & set(HIGH_SPOOF):
        return "likely_spoof"
    if codes & {"reported_spam", "staff_marked"} and any(s["severity"] != "info" for s in signals if s["code"] in ("reported_spam", "staff_marked")):
        return "likely_spam"
    if "trusted_known" in codes:
        return "trusted"
    return "known" if partner_match else "unknown"
