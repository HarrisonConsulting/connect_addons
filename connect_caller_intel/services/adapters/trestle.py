from .base import Adapter


class Trestle(Adapter):
    code = 'trestle'
    ready = False
    nanp_only = True

    def credential(self, env):
        return None
