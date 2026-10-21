from odoo import fields, models


class ConnectCallerIdentity(models.Model):
    _name = 'connect.caller.identity'
    _description = 'Caller Identity Hint'
    _order = 'confidence desc, id desc'

    _ref_uniq = models.Constraint(
        'UNIQUE (reputation_id, source_id, external_ref)',
        'This hint is already recorded for the source.')

    reputation_id = fields.Many2one(
        'connect.caller.reputation', required=True, ondelete='cascade', index=True,
        help='Number this hint is about.')
    source_id = fields.Many2one(
        'connect.caller.source', required=True, ondelete='restrict',
        help='Integration that returned the hint.')
    name = fields.Char(
        required=True,
        help='Name the source associates with the number. Unverified. '
             'It is never linked to a contact automatically.')
    kind = fields.Selection(
        [('business', 'Business'), ('person', 'Person'), ('unknown', 'Unknown')],
        default='unknown',
        help='Whether the source says the number belongs to a business or a person.')
    address = fields.Char(
        help='Address the source returned. Staff see it. '
             'It is never copied onto a contact automatically.')
    url = fields.Char(
        help='Public page behind the hint (Wikidata item, OSM object, Google Maps place).')
    external_ref = fields.Char(
        required=True, help='Id on the source side (QID, node/123, place_id), used to upsert.')
    attribution = fields.Char(
        required=True, help='Credit the source requires wherever the hint is shown.')
    confidence = fields.Float(
        help='Weight of the hint, 0 to 1. Public registries rank low and paid matches rank higher.')
    fetched_at = fields.Datetime(help='When the source returned the hint.')
    expires_at = fields.Datetime(
        index=True,
        help='When the cached hint must be dropped. The source\'s terms cap how long it may be kept.')
