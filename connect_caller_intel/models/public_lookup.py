from odoo import fields, models

from ..services.adapters.registry import ADAPTERS


class ConnectCallerWikidata(models.Model):
    _name = 'connect.caller.wikidata'
    _description = 'Wikidata Phone Number'
    _order = 'e164, qid'

    e164 = fields.Char(required=True, index=True, help='Phone number in E.164 form, from Wikidata property P1329.')
    qid = fields.Char(required=True, help='Wikidata item id that lists the number.')
    label = fields.Char(help='English label of the item. Fetched once, on the first match.')

    def _cron_import(self):
        return ADAPTERS['wikidata'].import_bulk(self.env)


class ConnectCallerOsm(models.Model):
    _name = 'connect.caller.osm'
    _description = 'OpenStreetMap Phone Number'
    _order = 'e164, osm_type, osm_id'

    e164 = fields.Char(required=True, index=True, help='Phone number in E.164 form, from the OpenStreetMap extract.')
    osm_type = fields.Selection(
        [('node', 'Node'), ('way', 'Way'), ('relation', 'Relation')], required=True,
        help='Kind of OpenStreetMap object that carries the number.')
    osm_id = fields.Integer(required=True, help='OpenStreetMap object id.')
    name = fields.Char(required=True, help='Name tag of the object.')
    lat = fields.Float(digits=(10, 7), help='Latitude of the object, when the extract has one.')
    lon = fields.Float(digits=(10, 7), help='Longitude of the object, when the extract has one.')

    def _cron_import(self):
        return ADAPTERS['osm'].import_extract(self.env)
