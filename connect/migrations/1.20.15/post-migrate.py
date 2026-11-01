import logging
from odoo import api, SUPERUSER_ID, Command

logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    VoicemailBox = env['connect.voicemail_box']
    users_without_box = env['connect.user'].search([('voicemail_box_id', '=', False)])
    logger.info('Creating voicemail boxes for %d connect users', len(users_without_box))

    # Verify the M2M junction table exists before using it
    cr.execute("SELECT to_regclass('connect_call_connect_user_rel')")
    junction_table = cr.fetchone()[0]

    for pbx_user in users_without_box:
        box_name = (pbx_user.user.name if pbx_user.user else pbx_user.username) or 'Voicemail'
        box = VoicemailBox.create({
            'name': box_name,
            'member_ids': [Command.link(pbx_user.user.id)] if pbx_user.user else [],
        })
        # Set directly via SQL to skip the write() override (migration only assigns
        # null-box calls; explicit admin action via UI overrides all)
        cr.execute('UPDATE connect_user SET voicemail_box_id = %s WHERE id = %s', (box.id, pbx_user.id))

        if junction_table:
            cr.execute("""
                UPDATE connect_call
                SET voicemail_box_id = %s
                WHERE voicemail_url IS NOT NULL
                AND voicemail_box_id IS NULL
                AND id IN (
                    SELECT connect_call_id
                    FROM connect_call_connect_user_rel
                    WHERE connect_user_id = %s
                )
            """, (box.id, pbx_user.id))
            logger.info(
                'Assigned %d voicemails to box "%s" for user %s',
                cr.rowcount, box_name, pbx_user.username,
            )
        else:
            logger.warning('Junction table connect_call_connect_user_rel not found; skipping voicemail backfill for user %s', pbx_user.username)
