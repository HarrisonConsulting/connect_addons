# -*- coding: utf-8 -*-
import logging
import boto3
from botocore.exceptions import ClientError
from odoo import fields, models
from odoo.exceptions import UserError

logger = logging.getLogger(__name__)


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
