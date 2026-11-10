# -*- coding: utf-8 -*-
import json
import logging
from datetime import timezone
from urllib.parse import urlparse

from twilio.base.exceptions import TwilioRestException

from odoo import fields, models, api

from odoo.addons.connect.models.settings import debug

logger = logging.getLogger(__name__)


class Recording(models.Model):
    _inherit = 'connect.recording'

    @api.model
    def prepare_data(self, rec):
        data = {}
        for field in [
            'sid',
            'call_sid',
            'media_url',
            'price',
            'price_unit',
            'duration',
            'source',
            'start_time',
            'status',
        ]:
            data[field] = getattr(rec, field)
            if field in ['start_time', 'date_created', 'date_updated']:
                value = data[field]
                if not value:
                    data[field] = False
                elif value.tzinfo is not None:
                    data[field] = value.astimezone(timezone.utc).replace(
                        tzinfo=None
                    )
        channel = self.env['connect.channel'].search(
            [('sid', '=', rec.call_sid)]
        )
        data['call'] = channel.call.id
        data['channel'] = channel.id
        return data

    def _twilio_owns_recording(self):
        """True when Twilio holds this recording: its media URL is on a Twilio host.

        With Twilio External Storage the media URL is the account's own bucket
        and Twilio keeps no copy, so there is nothing to delete there.
        """
        self.ensure_one()
        host = (urlparse(self.media_url or '').hostname or '').lower()
        return host == 'twilio.com' or host.endswith('.twilio.com')

    def _provider_owns_recording(self):
        self.ensure_one()
        return self._twilio_owns_recording() or super()._provider_owns_recording()

    def _provider_delete_recording(self):
        """DELETE Recordings/{sid} at Twilio. A 404 means it is already gone."""
        self.ensure_one()
        if not self._twilio_owns_recording():
            return super()._provider_delete_recording()
        client = self.env['connect.settings'].sudo().get_client()
        try:
            deleted = client.recordings(self.sid).delete()
        except TwilioRestException as error:
            if error.status == 404:
                return True
            raise
        return deleted is True

    def sync(self):
        client = self.env['connect.settings'].get_client()
        for rec in self:
            if not rec.media_url or rec.provider_deleted_at:
                continue
            recording = client.recordings(rec.sid).fetch()
            data = self.prepare_data(recording)
            rec.write(data)

    @api.private
    @api.model
    def on_recording_status(self, params):
        self = self.sudo()
        debug(
            self,
            'On recording status: %s' % json.dumps(params, indent=2),
        )
        data = {
            'sid': params['RecordingSid'],
            'call_sid': params['CallSid'],
            'duration': params['RecordingDuration'],
            'status': params['RecordingStatus'],
        }
        channel = self.env['connect.channel'].search(
            [('sid', '=', params['CallSid'])], limit=1
        )
        called_user = (
            channel.search(
                [
                    '|',
                    ('sid', '=', params['CallSid']),
                    ('parent_channel', '=', channel.id),
                    ('called_user', '!=', False),
                ],
                limit=1,
            ).called_user
        )
        if channel:
            call = channel.call
            data['channel'] = channel.id
            data['call'] = call.id
            data['partner'] = call.partner.id
            data['called_user'] = called_user.id
            data['caller_number'] = call.caller
            data['called_number'] = call.called
        # Fetch recording
        client = self.env['connect.settings'].get_client()
        try:
            recording = client.recordings(data['sid']).fetch()
            data.update(self.prepare_data(recording))
        except Exception as e:
            logger.exception('Recording fetch error: %s', e)
        self.create(data)
        return True
