from odoo import fields, models


class ConnectCallerNxx(models.Model):
    _name = 'connect.caller.nxx'
    _description = 'Caller Numbering Exchange'
    _rec_name = 'npa_nxx'
    _order = 'npa_nxx'

    _npa_nxx_uniq = models.Constraint('UNIQUE (npa_nxx)', 'An exchange is listed once.')

    npa_nxx = fields.Char(
        size=6, required=True, index=True,
        help='NANP area code and exchange, six digits, e.g. 601555.')
    npa = fields.Char(size=3, index=True, help='Area code of the exchange.')
    nxx = fields.Char(size=3, help='Exchange (central office code) within the area code.')
    state_code = fields.Char(
        help='US state or territory the exchange serves, per the numbering administrator.')
    status = fields.Selection(
        [('assigned', 'Assigned'), ('unavailable', 'Unavailable'), ('available', 'Available')],
        help='NANPA status: assigned to a carrier, unavailable for assignment, or available. '
             'A number in an available exchange cannot exist.')
    in_service = fields.Boolean(
        help='The administrator reports the assigned exchange as in service.')
    ocn = fields.Char(
        help='Operating Company Number of the code holder. Pooling and porting make it '
             'approximate per number.')
    company = fields.Char(
        help='Code holder named by the administrator. The number may have been ported since.')
    rate_center = fields.Char(help='Rate centre abbreviation the exchange is assigned to.')
    locality = fields.Char(help='Readable rate centre name from Local Calling Guide, when known.')
    lata = fields.Char(help='LATA of the exchange, from Local Calling Guide.')
    pooled = fields.Boolean(
        help='The exchange is split into thousands-blocks among carriers.')
    effective_date = fields.Date(help='Date the assignment took effect.')
    assign_date = fields.Date(help='Date the administrator assigned the code.')
    origin = fields.Selection(
        [('nanpa', 'NANPA'), ('lcg', 'Local Calling Guide')], required=True, default='nanpa',
        help='Which dataset created this row.')
    loaded_at = fields.Datetime(
        index=True,
        help='Bulk load that last confirmed this row. Rows from older loads are removed.')
    lcg_checked_at = fields.Datetime(
        help='When Local Calling Guide was last asked about this exchange. Cached for 30 days.')

    def _lookup_facts(self, npa_nxx):
        return {}

    def _cron_load_nanpa(self):
        return 0

    def _loaded_npas(self):
        return frozenset()
