# -*- coding: utf-8 -*-
import json
import logging
from email.utils import parsedate_to_datetime
from datetime import timezone

from odoo import fields, models, api

from odoo.addons.connect.models.settings import debug

logger = logging.getLogger(__name__)


class Recording(models.Model):
    _inherit = 'connect.recording'

    # connect_twilio defines prepare_data(), sync() and
    # on_recording_status() on this shared model without super(), so Odoo
    # would run only whichever module merged last for both providers.
    # VoiceML's carry voiceml in the name.

    @api.model
    def prepare_voiceml_data(self, rec):
        data = {}
        for field in [
            'sid', 'call_sid', 'media_url', 'price', 'price_unit',
            'duration', 'source', 'start_time', 'status',
        ]:
            data[field] = getattr(rec, field, None)
        data['start_time'] = self._voiceml_parse_start_time(data['start_time'])
        channel = self.env['connect.channel'].search(
            [('sid', '=', rec.call_sid)], limit=1)
        data['call'] = channel.call.id if channel else False
        data['channel'] = channel.id if channel else False
        return data

    @api.model
    def _voiceml_parse_start_time(self, value):
        """RFC 2822 string from the API to naive UTC datetime, or False."""
        if not value:
            return False
        try:
            dt = parsedate_to_datetime(str(value))
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except (TypeError, ValueError, IndexError):
            return False

    def sync_voiceml(self):
        client = self.env['connect.settings']._voiceml_client()
        for rec in self:
            if not rec.sid:
                continue
            try:
                recording = client.recordings.get(rec.sid)
                rec.write(self.prepare_voiceml_data(recording))
            except Exception as e:
                logger.exception('Recording fetch error: %s', e)

    @api.model
    def _on_recording_status_voiceml(self, params):
        self = self.sudo()
        debug(self, 'On recording status: %s' % json.dumps(params, indent=2))
        data = {
            'sid': params['RecordingSid'],
            'call_sid': params['CallSid'],
            'duration': params.get('RecordingDuration'),
            'status': params['RecordingStatus'],
        }
        channel = self.env['connect.channel'].search(
            [('sid', '=', params['CallSid'])], limit=1)
        if channel:
            call = channel.call
            data['channel'] = channel.id
            data['call'] = call.id
            data['partner'] = call.partner.id
            data['caller_number'] = call.caller
            data['called_number'] = call.called
        try:
            client = self.env['connect.settings']._voiceml_client()
            recording = client.recordings.get(data['sid'])
            data.update(self.prepare_voiceml_data(recording))
            if not data.get('media_url'):
                data['media_url'] = params.get('RecordingUrl')
        except Exception as e:
            logger.exception('Recording fetch error: %s', e)
            if params.get('RecordingUrl'):
                data['media_url'] = params['RecordingUrl']
        existing = self.search([('sid', '=', data['sid'])], limit=1)
        if existing:
            existing.write(data)
        else:
            self.create(data)
        return True
