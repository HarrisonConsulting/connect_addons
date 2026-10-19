"""Move recording files from connect_recording.attachment_id into recording_attachment.

The monolithic connect stored a downloaded recording as a plain ir.attachment
(res_field NULL) and pointed connect_recording.attachment_id at it. NG
connect reads the file from the attachment-backed binary field
``recording_attachment`` and no longer declares attachment_id, so Odoo drops
the column at the end of this upgrade and the files become unreachable.

Before that, turn each referenced attachment into the field attachment of
its recording: set res_model/res_field/res_id in place, so the filestore
blob is reused and nothing is copied. A recording that already has a
recording_attachment is left alone. An attachment shared by several
recordings is re-pointed for the first and duplicated (same store_fname)
for the rest.
"""

import logging

from odoo.tools.sql import column_exists

_logger = logging.getLogger(__name__)

MODEL = 'connect.recording'
FIELD = 'recording_attachment'


def migrate(cr, version):
    if not version:
        return
    if not column_exists(cr, 'connect_recording', 'attachment_id'):
        return

    # Recordings that point at an attachment and have no field attachment yet.
    cr.execute(
        """
        CREATE TEMP TABLE _connect_recording_move ON COMMIT DROP AS
        SELECT r.id AS recording_id, r.attachment_id,
               row_number() OVER (PARTITION BY r.attachment_id ORDER BY r.id) AS n
          FROM connect_recording r
          JOIN ir_attachment a ON a.id = r.attachment_id
         WHERE NOT EXISTS (
                SELECT 1 FROM ir_attachment b
                 WHERE b.res_model = %s AND b.res_field = %s AND b.res_id = r.id)
        """,
        (MODEL, FIELD),
    )

    cr.execute(
        """
        UPDATE ir_attachment a
           SET res_model = %s, res_field = %s, res_id = m.recording_id
          FROM _connect_recording_move m
         WHERE m.n = 1 AND a.id = m.attachment_id
        """,
        (MODEL, FIELD),
    )
    moved = cr.rowcount

    cr.execute(
        """
        INSERT INTO ir_attachment (
            name, description, res_model, res_field, res_id, company_id,
            type, url, public, access_token, db_datas, store_fname,
            file_size, checksum, mimetype, index_content,
            create_uid, create_date, write_uid, write_date)
        SELECT a.name, a.description, %s, %s, m.recording_id, a.company_id,
               a.type, a.url, a.public, NULL, a.db_datas, a.store_fname,
               a.file_size, a.checksum, a.mimetype, a.index_content,
               a.create_uid, a.create_date, a.write_uid, a.write_date
          FROM _connect_recording_move m
          JOIN ir_attachment a ON a.id = m.attachment_id
         WHERE m.n > 1
        """,
        (MODEL, FIELD),
    )
    copied = cr.rowcount

    cr.execute("DROP TABLE _connect_recording_move")
    _logger.info(
        'connect: moved %s recording files into %s (%s shared copies)',
        moved + copied, FIELD, copied)
