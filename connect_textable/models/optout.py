from odoo import fields, models


class TextableOptout(models.Model):
    """Keyword changes this module made to phone.blacklist.

    Opt-outs are per sender number: a START lifts only the STOPs sent to
    the same number. phone.blacklist is global, so its entry is removed
    only once no STOP through any number still stands, and only when a row
    here shows that a STOP through this module created or re-activated it;
    entries made elsewhere are left alone. The rows also count against the
    per-number cap.
    """
    _name = 'connect.textable.optout'
    _description = 'Textable Opt-out Keyword'
    _order = 'id desc'
    _rec_name = 'phone'

    phone = fields.Char(
        required=True, readonly=True, index=True,
        help='The contact number that sent the keyword, E.164.')
    number_id = fields.Many2one(
        'connect.textable.number', required=True, readonly=True,
        ondelete='cascade', index=True,
        help='The owned number the keyword was sent to.')
    company_id = fields.Many2one(
        'res.company', required=True, readonly=True, index=True,
        help='Company of the owned number.')
    action = fields.Selection(
        [('stop', 'Opt-out'), ('start', 'Opt-in')], required=True, readonly=True,
        help='What the keyword asked for.')
    keyword = fields.Char(
        readonly=True,
        help='The keyword as received, upper-cased.')
    created_entry = fields.Boolean(
        readonly=True,
        help='This opt-out created or re-activated the blacklist entry, so a '
             'later START may remove it.')
    lifted = fields.Boolean(
        readonly=True,
        help='A later START to the same number lifted this opt-out.')
    released = fields.Boolean(
        readonly=True,
        help='The blacklist entry this opt-out made was removed, once no '
             'opt-out through any number still stood.')
    capped = fields.Boolean(
        readonly=True,
        help='The per-number cap was reached; the blacklist was not changed.')
    message_id = fields.Many2one(
        'connect.message', readonly=True, ondelete='set null',
        help='The inbound message that carried the keyword.')
