"""Offline numbering-plan facts from phonenumbers metadata."""

import re

import phonenumbers
from phonenumbers import PhoneNumberType as T
from phonenumbers import carrier as pn_carrier
from phonenumbers import geocoder

LINE_TYPES = {
    T.MOBILE: "mobile",
    T.FIXED_LINE: "fixed",
    T.FIXED_LINE_OR_MOBILE: "fixed",
    T.VOIP: "voip",
    T.TOLL_FREE: "toll_free",
    T.PREMIUM_RATE: "premium",
    T.SHARED_COST: "shared_cost",
}

_N11 = re.compile(r"^[2-9]11$")
_555_RESERVED = re.compile(r"^555(01\d\d)$")


def nanp_flags(national):
    """Classify a 10-digit NANP national number: n11, bad_nxx, reserved_555."""
    digits = re.sub(r"\D", "", national or "")
    if len(digits) != 10:
        return {"n11": False, "bad_nxx": False, "reserved_555": False}
    npa, nxx, line = digits[:3], digits[3:6], digits[6:]
    return {
        "n11": bool(_N11.match(npa)),
        "bad_nxx": nxx[0] in "01",
        "reserved_555": nxx == "555" and line.startswith("01"),
    }


def analyze(raw, default_region="US"):
    """Return offline facts for a number; never touches the network."""
    facts = {
        "e164": "", "valid": False, "line_type": "unknown", "country_code": "",
        "calling_code": None, "region": "", "carrier": "", "npanxx": "",
        "n11": False, "bad_nxx": False, "reserved_555": False,
    }
    try:
        num = phonenumbers.parse(raw, default_region)
    except phonenumbers.NumberParseException:
        return facts
    facts["e164"] = phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
    facts["valid"] = phonenumbers.is_valid_number(num)
    facts["line_type"] = LINE_TYPES.get(phonenumbers.number_type(num), "unknown")
    facts["country_code"] = phonenumbers.region_code_for_number(num) or ""
    facts["calling_code"] = num.country_code
    facts["region"] = geocoder.description_for_number(num, "en")
    facts["carrier"] = pn_carrier.name_for_number(num, "en")
    if num.country_code == 1:
        national = str(num.national_number)
        facts["npanxx"] = national[:6] if len(national) == 10 else ""
        facts.update(nanp_flags(national))
    return facts
