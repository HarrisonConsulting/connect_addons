from .base import Adapter


class WikidataPhone(Adapter):
    code = 'wikidata'
    ready = False
    nanp_only = False

    def credential(self, env):
        return None
