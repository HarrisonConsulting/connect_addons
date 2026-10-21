from .base import Adapter


class LocalCallingGuide(Adapter):
    code = 'local_calling_guide'
    ready = False
    nanp_only = True

    def credential(self, env):
        return None
