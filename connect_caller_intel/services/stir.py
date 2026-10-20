"""Parse Twilio's StirVerstat into an attestation level and a verified flag."""

ANONYMOUS_SENTINELS = frozenset({"anonymous", "restricted", "unavailable", "unknown", "private", "blocked", "266696687", "7378742833"})


def parse_stir_verstat(value):
    """Return {'attestation': 'A'|'B'|'C'|'failed'|'none', 'verified': bool, 'raw': str}.

    Passed values carry the attestation letter; failed validation maps to
    'failed'; a missing value or "No-TN-Validation" maps to 'none'.
    """
    raw = (value or "").strip()
    low = raw.lower()
    if low.startswith("tn-validation-passed-"):
        letter = raw[-1].upper()
        if letter in ("A", "B", "C"):
            return {"attestation": letter, "verified": True, "raw": raw}
        return {"attestation": "none", "verified": False, "raw": raw}
    if low.startswith("tn-validation-failed"):
        return {"attestation": "failed", "verified": False, "raw": raw}
    return {"attestation": "none", "verified": False, "raw": raw}


def is_anonymous_caller(caller):
    """True for an empty caller or an anonymous/restricted sentinel."""
    text = (caller or "").strip().lstrip("+").lower()
    return not text or text in ANONYMOUS_SENTINELS
