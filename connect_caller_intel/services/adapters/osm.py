from .base import Adapter


class OsmPhone(Adapter):
    code = 'osm'
    ready = False
    nanp_only = True

    def credential(self, env):
        return None
