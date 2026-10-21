from .base import Adapter


class GooglePlaces(Adapter):
    code = 'google_places'
    ready = False
    nanp_only = False

    def credential(self, env):
        return None
