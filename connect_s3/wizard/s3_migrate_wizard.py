# -*- coding: utf-8 -*-
from odoo import api, fields, models


class S3MigrateWizard(models.TransientModel):
    _name = 'connect.s3.migrate.wizard'
    _description = 'Migrate Recordings to S3'

    recording_twilio_count = fields.Integer(string='Recordings on Twilio', readonly=True)
    recording_filestore_count = fields.Integer(string='Recordings in filestore', readonly=True)
    voicemail_count = fields.Integer(string='Voicemails on Twilio', readonly=True)
    total_count = fields.Integer(
        string='Total',
        compute='_compute_total_count',
    )
    delete_source = fields.Boolean(
        string='Delete source after migration',
        help='Respects the global "Delete recording from Twilio" setting — enable that setting to delete originals after a successful verified upload.',
        readonly=True,
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        Recording = self.env['connect.recording'].sudo()
        Call = self.env['connect.call'].sudo()
        res['recording_twilio_count'] = Recording.search_count([
            ('s3_key', '=', False),
            ('media_url', '!=', False),
            ('attachment_id', '=', False),
        ])
        res['recording_filestore_count'] = Recording.search_count([
            ('s3_key', '=', False),
            ('attachment_id', '!=', False),
        ])
        res['voicemail_count'] = Call.search_count([
            ('voicemail_s3_key', '=', False),
            ('voicemail_url', '!=', False),
        ])
        settings = self.env['connect.settings'].sudo().search([], limit=1)
        res['delete_source'] = bool(settings and settings.delete_twilio_recording)
        return res

    @api.depends('recording_twilio_count', 'recording_filestore_count', 'voicemail_count')
    def _compute_total_count(self):
        for rec in self:
            rec.total_count = (
                rec.recording_twilio_count
                + rec.recording_filestore_count
                + rec.voicemail_count
            )

    def action_start_migration(self):
        cron = self.env.ref('connect_s3.ir_cron_s3_migration')
        cron.sudo().write({
            'active': True,
            'nextcall': fields.Datetime.now(),
        })
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Migration Started',
                'message': (
                    'Migrating {} items in batches of 20. '
                    'Monitor progress under Settings → Technical → Scheduled Actions.'
                ).format(self.total_count),
                'type': 'success',
                'sticky': False,
            },
        }
