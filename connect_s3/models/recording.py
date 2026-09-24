# -*- coding: utf-8 -*-
import base64
import logging
import os

from botocore.exceptions import BotoCoreError, ClientError
from odoo import api, fields, models
from odoo.exceptions import AccessError

logger = logging.getLogger(__name__)
S3_KEY_PREFIX = 'connect/recordings'


class Recording(models.Model):
    _inherit = 'connect.recording'

    s3_key = fields.Char(string='S3 Key', readonly=True, copy=False)
    storage_pending = fields.Boolean(
        copy=False, help='The storage cron still needs to archive this recording.')
    storage_error = fields.Char(
        readonly=True, copy=False, help='Last archival failure; provider media is retained.')

    def _s3_object_key(self):
        self.ensure_one()
        suffix = os.path.splitext(self.recording_filename or '')[1] or '.wav'
        return '{}/{}/{}{}'.format(S3_KEY_PREFIX, self.env.cr.dbname, self.sid or self.id, suffix)

    def _queue_storage(self):
        settings = self.env['connect.settings'].sudo().search([], limit=1)
        if settings.recording_storage == 's3':
            self.filtered(lambda rec: not rec.s3_key and rec.status == 'completed'
                          and (rec.media_url or rec.recording_attachment)).with_context(
                              connect_storage_write=True).write({'storage_pending': True})

    def action_retry_storage(self):
        if not self.env.user.has_group('base.group_system'):
            raise AccessError('Only administrators can retry recording archival.')
        self._queue_storage()

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._queue_storage()
        return records

    def write(self, vals):
        result = super().write(vals)
        if not self.env.context.get('connect_storage_write') and {
                'status', 'media_url', 'recording_attachment'}.intersection(vals):
            self._queue_storage()
        return result

    def _fetch_media_to(self, temp_file):
        """All media consumers share NG's provider/storage download seam."""
        self.ensure_one()
        if self.s3_key:
            settings = self.env['connect.settings'].sudo().search([], limit=1)
            try:
                settings.get_s3_client().download_fileobj(
                    settings.s3_bucket, self.s3_key, temp_file)
                return
            except (ClientError, BotoCoreError):
                temp_file.seek(0)
                temp_file.truncate()
                if not (self.media_url or self.recording_attachment):
                    raise
                logger.warning('S3 read failed for recording %s; trying retained source', self.id)
        return super()._fetch_media_to(temp_file)

    def _migrate_to_s3(self, settings):
        """Archive exact source bytes; retain the provider copy during recovery."""
        self.ensure_one()
        if self.s3_key:
            return
        path = self._download_recording_audio()
        try:
            size = os.path.getsize(path)
            if not size:
                raise ValueError('Recording download was empty')
            key = self._s3_object_key()
            client = settings.get_s3_client()
            try:
                with open(path, 'rb') as media:
                    signature = media.read(12)
                    media.seek(0)
                    mimetype = 'audio/wav' if signature.startswith(b'RIFF') else 'audio/mpeg'
                    client.upload_fileobj(media, settings.s3_bucket, key,
                                          ExtraArgs={'ContentType': mimetype})
                if client.head_object(Bucket=settings.s3_bucket, Key=key).get('ContentLength') != size:
                    raise ValueError('S3 stored size differs from downloaded recording')
            except (ClientError, BotoCoreError, ValueError):
                with open(path, 'rb') as media:
                    self.with_context(connect_storage_write=True).write({
                        'recording_attachment': base64.b64encode(media.read()),
                        'recording_filename': os.path.basename(path),
                    })
                raise
            self.with_context(connect_storage_write=True).write({
                's3_key': key, 'storage_pending': False, 'storage_error': False,
            })
        finally:
            os.unlink(path)

    @api.model
    def _cron_store_recordings(self, limit=20):
        settings = self.env['connect.settings'].sudo().search([], limit=1)
        if settings.recording_storage != 's3':
            return
        for rec in self.search([('storage_pending', '=', True)], limit=limit):
            try:
                rec._migrate_to_s3(settings)
            except Exception as error:
                logger.exception('Recording archival failed for recording %s', rec.id)
                rec.storage_error = type(error).__name__
            rec.storage_pending = False
            if not self.env['ir.cron']._commit_progress(1):
                break

    def _get_media_src(self, proxy_recordings):
        self.ensure_one()
        if self.s3_key:
            settings = self.env['connect.settings'].sudo().search([], limit=1)
            try:
                return settings.get_s3_client().generate_presigned_url(
                    'get_object', Params={'Bucket': settings.s3_bucket, 'Key': self.s3_key},
                    ExpiresIn=28800)
            except (ClientError, BotoCoreError, ValueError):
                logger.warning('S3 playback URL failed for recording %s; trying retained source', self.id)
        return super()._get_media_src(proxy_recordings)

    def unlink(self):
        keys = self.mapped('s3_key')
        result = super().unlink()
        if keys:
            try:
                settings = self.env['connect.settings'].sudo().search([], limit=1)
                client = settings.get_s3_client()
                for key in keys:
                    try:
                        client.delete_object(Bucket=settings.s3_bucket, Key=key)
                    except Exception:
                        logger.exception('S3 cleanup failed for one deleted recording')
            except Exception:
                logger.exception('S3 cleanup failed after recording unlink')
        return result
