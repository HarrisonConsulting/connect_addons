"""Orphan dropped partner xmlids so _process_end does not unlink them.

NG no longer ships connect/data/res_partner.xml (Oduist vendor contact,
echo test, callerid test). Odoo then tries to DELETE those res.partner
rows. Production has already used connect_oduist_contact on
account_bank_statement_line, so the unlink ForeignKeyViolation kills
the registry. Deleting the xmlid leaves the partner in place.
"""

import logging

_logger = logging.getLogger(__name__)

KEEP_PARTNER_XMLIDS = ('partner_connect_webhook',)


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        """
        DELETE FROM ir_model_data
         WHERE module = 'connect'
           AND model = 'res.partner'
           AND name NOT IN %s
        """,
        [KEEP_PARTNER_XMLIDS],
    )
    if cr.rowcount:
        _logger.info(
            'orphaned %s dropped connect res.partner xmlid(s)',
            cr.rowcount,
        )
