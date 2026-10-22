from .fcc_complaints import FccComplaints
from .ftc_dnc import FtcDnc
from .google_places import GooglePlaces
from .ipqs import Ipqs
from .local_calling_guide import LocalCallingGuide
from .osm import OsmPhone
from .telnyx_lookup import TelnyxLookup
from .trestle import Trestle
from .twilio_lookup import TwilioLookup
from .wikidata import WikidataPhone

ADAPTERS = {
    adapter.code: adapter
    for adapter in (
        FtcDnc(),
        FccComplaints(),
        TwilioLookup('twilio_lookup_line_type', 'line_type_intelligence'),
        TwilioLookup('twilio_lookup_cnam', 'caller_name'),
        LocalCallingGuide(),
        WikidataPhone(),
        OsmPhone(),
        GooglePlaces(),
        TelnyxLookup(),
        Ipqs(),
        Trestle(),
    )
}
