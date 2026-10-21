from .base import Adapter


class TelnyxLookup(Adapter):
    code = 'telnyx_lookup'
    ready = False
    nanp_only = False

    def credential(self, env):
        return None
