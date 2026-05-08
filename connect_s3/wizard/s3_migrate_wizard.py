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
    last_processed = fields.Integer(string='Migrated in last batch', readonly=True)

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
        """Run one batch synchronously. Each call is idempotent — only records
        missing an s3_key are picked up, so re-clicking resumes from where the
        last batch left off (or a failure stopped). User clicks again to
        process the next batch until nothing remains."""
        self.ensure_one()
        processed = self.env['connect.settings']._s3_migration_batch()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'connect.s3.migrate.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_last_processed': processed},
        }
