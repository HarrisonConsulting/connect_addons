# -*- coding: utf-8 -*-
import base64
import logging
import requests
from io import BytesIO
from tempfile import NamedTemporaryFile
from botocore.exceptions import BotoCoreError, ClientError
from odoo import fields, models

logger = logging.getLogger(__name__)

S3_KEY_PREFIX = 'connect/recordings'


class Recording(models.Model):
    _inherit = 'connect.recording'

    s3_key = fields.Char(string='S3 Key', readonly=True, copy=False)

    def _s3_object_key(self):
        self.ensure_one()
        return '{}/{}/{}.mp3'.format(S3_KEY_PREFIX, self.env.cr.dbname, self.sid)

    def _store_as_attachment(self):
        """Upload recording to S3 when recording_storage == 's3'."""
        settings = self.env['connect.settings'].sudo().search([], limit=1)
        if settings.recording_storage != 's3':
            return super()._store_as_attachment()
        self.ensure_one()
        if not self.media_url:
            return
        from connect.models.settings import HTTP_DOWNLOAD_TIMEOUT
        account_sid = self.env['connect.settings'].sudo().get_param('account_sid')
        auth_token = self.env['connect.settings'].sudo().get_param('auth_token')
        response = requests.get(
            self.media_url, auth=(account_sid, auth_token),
            timeout=HTTP_DOWNLOAD_TIMEOUT,
        )
        response.raise_for_status()
        s3 = settings.get_s3_client()
        bucket = settings.s3_bucket
        key = self._s3_object_key()
        try:
            s3.upload_fileobj(
                BytesIO(response.content), bucket, key,
                ExtraArgs={'ContentType': 'audio/mpeg'},
            )
        except (ClientError, BotoCoreError) as e:
            logger.error('S3 upload failed for recording %s, falling back to attachment: %s', self.sid, e)
            return super()._store_as_attachment()
        self.write({'s3_key': key})
        if settings.delete_twilio_recording:
            if settings._s3_object_verified(s3, bucket, key):
                self._delete_from_twilio()
            else:
                logger.warning('S3 verify failed for recording %s — skipping Twilio delete', self.sid)
        logger.info('Recording %s stored in S3: %s/%s', self.sid, bucket, key)

    def _download_recording_audio(self):
        """Fetch audio from S3 when s3_key is set."""
        if self.s3_key:
            settings = self.env['connect.settings'].sudo().search([], limit=1)
            try:
                s3 = settings.get_s3_client()
                buf = BytesIO()
                s3.download_fileobj(settings.s3_bucket, self.s3_key, buf)
                with NamedTemporaryFile(delete=False, suffix='.mp3') as f:
                    f.write(buf.getvalue())
                    return f.name
            except (ClientError, BotoCoreError) as e:
                logger.error('S3 download failed for recording %s (%s): %s', self.id, self.s3_key, e)
        return super()._download_recording_audio()

    def _get_recording_widget(self):
        """Render widget with presigned S3 URL when s3_key is set."""
        settings = self.env['connect.settings'].sudo().search([], limit=1)
        if settings.recording_storage != 's3':
            return super()._get_recording_widget()
        for rec in self:
            if not rec.s3_key:
                rec.recording_widget = ''
                continue
            try:
                s3 = settings.get_s3_client()
                url = s3.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': settings.s3_bucket, 'Key': rec.s3_key},
                    ExpiresIn=28800,
                )
                rec.recording_widget = (
                    '<audio id="sound_file" preload="auto" controls="controls">'
                    '<source src="{}"/></audio>'.format(url)
                )
            except Exception as e:
                logger.error('S3 presigned URL error for recording %s: %s', rec.id, e)
                rec.recording_widget = ''

    def _migrate_to_s3(self, settings):
        """Migrate a single recording to S3 from Twilio or local filestore."""
        self.ensure_one()
        s3 = settings.get_s3_client()
        bucket = settings.s3_bucket
        key = self._s3_object_key()

        if self.attachment_id:
            audio = base64.b64decode(self.attachment_id.sudo().datas)
        else:
            from connect.models.settings import HTTP_DOWNLOAD_TIMEOUT
            account_sid = self.env['connect.settings'].sudo().get_param('account_sid')
            auth_token = self.env['connect.settings'].sudo().get_param('auth_token')
            resp = requests.get(
                self.media_url, auth=(account_sid, auth_token),
                timeout=HTTP_DOWNLOAD_TIMEOUT,
            )
            resp.raise_for_status()
            audio = resp.content

        s3.upload_fileobj(BytesIO(audio), bucket, key, ExtraArgs={'ContentType': 'audio/mpeg'})

        if not settings._s3_object_verified(s3, bucket, key):
            raise Exception('S3 verify failed after upload for recording {}'.format(self.id))

        source_attachment = self.attachment_id
        self.write({'s3_key': key})

        if settings.delete_twilio_recording:
            if source_attachment:
                source_attachment.sudo().unlink()
            elif self.sid:
                self._delete_from_twilio()

        logger.info('Migrated recording %s to S3: %s/%s', self.id, bucket, key)

    def unlink(self):
        """Delete S3 objects when recordings are deleted."""
        s3_recs = self.filtered('s3_key')
        result = super().unlink()
        if s3_recs:
            try:
                settings = self.env['connect.settings'].sudo().search([], limit=1)
                s3 = settings.get_s3_client()
                bucket = settings.s3_bucket
                for rec in s3_recs:
                    try:
                        s3.delete_object(Bucket=bucket, Key=rec.s3_key)
                    except Exception as e:
                        logger.error('Failed to delete S3 object %s: %s', rec.s3_key, e)
            except Exception as e:
                logger.error('S3 cleanup error on recording unlink: %s', e)
        return result
