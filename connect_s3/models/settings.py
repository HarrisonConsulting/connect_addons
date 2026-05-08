# -*- coding: utf-8 -*-
import logging
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from odoo import fields, models
from odoo.exceptions import UserError

logger = logging.getLogger(__name__)

MIGRATION_BATCH = 20


class Settings(models.Model):
    _inherit = 'connect.settings'

    recording_storage = fields.Selection(
        selection_add=[('s3', 'S3 / Object Storage')],
        ondelete={'s3': 'set default'},
    )
    s3_endpoint = fields.Char(
        string='S3 Endpoint URL',
        help='Leave empty for AWS S3. Set for MinIO or other S3-compatible services (e.g. https://s3.example.com).',
    )
    s3_bucket = fields.Char(string='S3 Bucket')
    s3_access_key = fields.Char(
        string='S3 Access Key',
        groups='base.group_erp_manager',
    )
    s3_secret_key = fields.Char(
        string='S3 Secret Key',
        groups='base.group_erp_manager',
    )
    s3_region = fields.Char(string='S3 Region', default='us-east-1')

    def get_s3_client(self):
        """Return a configured boto3 S3 client."""
        self.ensure_one()
        settings = self.sudo()
        kwargs = {
            'aws_access_key_id': settings.s3_access_key,
            'aws_secret_access_key': settings.s3_secret_key,
            'region_name': settings.s3_region or 'us-east-1',
        }
        if settings.s3_endpoint:
            kwargs['endpoint_url'] = settings.s3_endpoint
        return boto3.client('s3', **kwargs)

    def action_test_s3_connection(self):
        """Verify S3 credentials and bucket access."""
        settings = self.sudo()
        bucket = settings.s3_bucket
        if not bucket:
            raise UserError('S3 bucket is not configured.')
        try:
            client = self.get_s3_client()
            client.head_bucket(Bucket=bucket)
        except ClientError as e:
            code = e.response['Error']['Code']
            if code == '403':
                raise UserError('S3 access denied — check your access key and bucket permissions.')
            if code == '404':
                raise UserError('Bucket "{}" not found.'.format(bucket))
            raise UserError('S3 error ({}): {}'.format(code, e.response['Error']['Message']))
        except BotoCoreError as e:
            raise UserError('S3 connection failed: {}'.format(e))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'S3 Connection OK',
                'message': 'Successfully connected to bucket "{}".'.format(bucket),
                'type': 'success',
            },
        }

    def _s3_object_verified(self, s3_client, bucket, key):
        """Return True only if the object exists in S3 with non-zero size."""
        try:
            head = s3_client.head_object(Bucket=bucket, Key=key)
            return head.get('ContentLength', 0) > 0
        except (ClientError, BotoCoreError):
            return False

    def action_open_migrate_wizard(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'connect.s3.migrate.wizard',
            'view_mode': 'form',
            'target': 'new',
        }

    def _s3_migration_batch(self, limit=MIGRATION_BATCH):
        """Process up to `limit` pending S3 migrations and return how many were
        actually moved. Idempotent: each call picks up only records still
        missing an s3_key, so partial progress survives interruption — re-run
        to resume."""
        settings = self.sudo().search([], limit=1)
        if not settings or settings.recording_storage != 's3':
            return 0

        processed = 0
        recordings = self.env['connect.recording'].sudo().search([
            ('s3_key', '=', False),
            '|',
            ('media_url', '!=', False),
            ('attachment_id', '!=', False),
        ], limit=limit)
        for rec in recordings:
            try:
                rec._migrate_to_s3(settings)
                processed += 1
            except Exception as e:
                logger.error('S3 migration failed for recording %s: %s', rec.id, e)

        remaining = limit - processed
        if remaining > 0:
            calls = self.env['connect.call'].sudo().search([
                ('voicemail_s3_key', '=', False),
                ('voicemail_url', '!=', False),
            ], limit=remaining)
            for call in calls:
                try:
                    call._migrate_voicemail_to_s3(settings)
                    processed += 1
                except Exception as e:
                    logger.error('S3 migration failed for voicemail call %s: %s', call.id, e)

        return processed
