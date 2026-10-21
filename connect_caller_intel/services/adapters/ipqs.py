from .base import Adapter


class Ipqs(Adapter):
    code = 'ipqs'
    ready = False
    nanp_only = False

    def credential(self, env):
        return None
