# -*- coding: utf-8 -*-
import base64
import logging
import requests
from io import BytesIO
from botocore.exceptions import BotoCoreError, ClientError
from odoo import fields, models

logger = logging.getLogger(__name__)

S3_VOICEMAIL_PREFIX = 'connect/voicemails'


class Call(models.Model):
    _inherit = 'connect.call'

    voicemail_s3_key = fields.Char(string='Voicemail S3 Key', readonly=True, copy=False)

    def _voicemail_s3_object_key(self):
        self.ensure_one()
        sid = self.voicemail_sid or str(self.id)
        return '{}/{}/{}.mp3'.format(S3_VOICEMAIL_PREFIX, self.env.cr.dbname, sid)

    def _store_voicemail_as_attachment(self):
        """Upload voicemail to S3 when recording_storage == 's3'."""
        settings = self.env['connect.settings'].sudo().search([], limit=1)
        if settings.recording_storage != 's3':
            return super()._store_voicemail_as_attachment()
        self.ensure_one()
        if not self.voicemail_url:
            return
        s3 = settings.get_s3_client()
        bucket = settings.s3_bucket
        key = self._voicemail_s3_object_key()
        # Retry fallback: a previous attempt may have uploaded to S3 (and
        # deleted the provider original) before its transaction failed —
        # HTTP side effects don't roll back. If the object is already
        # there, adopt it instead of re-downloading a recording that may
        # no longer exist.
        if settings._s3_object_verified(s3, bucket, key):
            self.write({'voicemail_s3_key': key})
            logger.info('Voicemail already in S3, adopted: %s/%s', bucket, key)
            return
        from odoo.addons.connect.models.settings import HTTP_DOWNLOAD_TIMEOUT
        account_sid = self.env['connect.settings'].sudo().get_param('account_sid')
        auth_token = self.env['connect.settings'].sudo().get_param('auth_token')
        response = requests.get(
            self.voicemail_url, auth=(account_sid, auth_token),
            timeout=HTTP_DOWNLOAD_TIMEOUT,
        )
        response.raise_for_status()
        try:
            s3.upload_fileobj(
                BytesIO(response.content), bucket, key,
                ExtraArgs={'ContentType': 'audio/mpeg'},
            )
        except (ClientError, BotoCoreError) as e:
            logger.error('S3 upload failed for voicemail %s, falling back to attachment: %s', self.id, e)
            return super()._store_voicemail_as_attachment()
        self.write({'voicemail_s3_key': key})
        if settings.delete_twilio_recording and self.voicemail_sid:
            if settings._s3_object_verified(s3, bucket, key):
                # Post-commit: never destroy the provider original inside a
                # transaction that could still fail and forget the S3 key.
                settings.defer_twilio_recording_delete(self.voicemail_sid)
            else:
                logger.warning('S3 verify failed for voicemail %s — skipping Twilio delete', self.voicemail_sid)
        logger.info('Voicemail stored in S3: %s/%s', bucket, key)

    def _download_voicemail_audio(self):
        """Serve the S3 copy when one exists.

        Checks object presence by computed key (not just voicemail_s3_key):
        a failed webhook transaction can leave the object in S3 with no key
        on the record — self-heal the key when that happens. Falls back to
        the core implementation (attachment / provider URL) otherwise.
        """
        self.ensure_one()
        settings = self.env['connect.settings'].sudo().search([], limit=1)
        if settings.recording_storage != 's3':
            return super()._download_voicemail_audio()
        s3 = settings.get_s3_client()
        bucket = settings.s3_bucket
        key = self.voicemail_s3_key or self._voicemail_s3_object_key()
        if not settings._s3_object_verified(s3, bucket, key):
            return super()._download_voicemail_audio()
        if not self.voicemail_s3_key:
            self.write({'voicemail_s3_key': key})
            logger.info('Voicemail S3 key self-healed for call %s: %s', self.id, key)
        from tempfile import NamedTemporaryFile
        with NamedTemporaryFile(delete=False, suffix='.mp3') as f:
            s3.download_fileobj(bucket, key, f)
            return f.name

    def _get_voicemail_listen_url(self):
        """Return a presigned S3 URL when voicemail is stored in S3."""
        if not self.voicemail_s3_key:
            return super()._get_voicemail_listen_url()
        self.ensure_one()
        try:
            settings = self.env['connect.settings'].sudo().search([], limit=1)
            s3 = settings.get_s3_client()
            return s3.generate_presigned_url(
                'get_object',
                Params={'Bucket': settings.s3_bucket, 'Key': self.voicemail_s3_key},
                ExpiresIn=604800,  # 7 days for email links
            )
        except Exception as e:
            logger.error('S3 presigned URL error for voicemail email %s: %s', self.id, e)
            return None

    def _get_voicemail_widget(self):
        """Render widget with presigned S3 URL when voicemail_s3_key is set."""
        settings = self.env['connect.settings'].sudo().search([], limit=1)
        if settings.recording_storage != 's3':
            return super()._get_voicemail_widget()
        for rec in self:
            if not rec.voicemail_s3_key:
                rec.voicemail_widget = ''
                continue
            try:
                s3 = settings.get_s3_client()
                url = s3.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': settings.s3_bucket, 'Key': rec.voicemail_s3_key},
                    ExpiresIn=28800,  # 8 hours for in-app playback
                )
                rec.voicemail_widget = (
                    '<audio id="sound_file" preload="auto" controls="controls">'
                    '<source src="{}"/></audio>'.format(url)
                )
            except Exception as e:
                logger.error('S3 presigned URL error for voicemail widget %s: %s', rec.id, e)
                rec.voicemail_widget = ''

    def _migrate_voicemail_to_s3(self, settings):
        """Migrate a single voicemail to S3 from Twilio or local filestore."""
        self.ensure_one()
        s3 = settings.get_s3_client()
        bucket = settings.s3_bucket
        key = self._voicemail_s3_object_key()

        if self.voicemail_attachment_id:
            audio = base64.b64decode(self.voicemail_attachment_id.sudo().datas)
        else:
            from odoo.addons.connect.models.settings import HTTP_DOWNLOAD_TIMEOUT
            account_sid = self.env['connect.settings'].sudo().get_param('account_sid')
            auth_token = self.env['connect.settings'].sudo().get_param('auth_token')
            resp = requests.get(
                self.voicemail_url, auth=(account_sid, auth_token),
                timeout=HTTP_DOWNLOAD_TIMEOUT,
            )
            resp.raise_for_status()
            audio = resp.content

        s3.upload_fileobj(BytesIO(audio), bucket, key, ExtraArgs={'ContentType': 'audio/mpeg'})

        if not settings._s3_object_verified(s3, bucket, key):
            raise Exception('S3 verify failed after upload for voicemail call {}'.format(self.id))

        source_attachment = self.voicemail_attachment_id
        self.write({'voicemail_s3_key': key})

        if settings.delete_twilio_recording:
            if source_attachment:
                source_attachment.sudo().unlink()
            elif self.voicemail_sid:
                try:
                    client = self.env['connect.settings'].get_client()
                    if client:
                        client.recordings(self.voicemail_sid).delete()
                        logger.info('Deleted voicemail %s from Twilio', self.voicemail_sid)
                except Exception as e:
                    logger.error('Failed to delete voicemail %s from Twilio: %s', self.voicemail_sid, e)

        logger.info('Migrated voicemail call %s to S3: %s/%s', self.id, bucket, key)
