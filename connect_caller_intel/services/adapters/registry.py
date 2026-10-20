from .fcc_complaints import FccComplaints
from .ftc_dnc import FtcDnc
from .twilio_lookup import TwilioLookup

ADAPTERS = {
    adapter.code: adapter
    for adapter in (
        FtcDnc(),
        FccComplaints(),
        TwilioLookup('twilio_lookup_line_type', 'line_type_intelligence'),
        TwilioLookup('twilio_lookup_cnam', 'caller_name'),
    )
}
