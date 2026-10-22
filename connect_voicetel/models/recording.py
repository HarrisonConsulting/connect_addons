# -*- coding: utf-8 -*-
import base64
import json
import logging
from urllib.parse import urljoin

import requests

from odoo import models, api

from odoo.addons.connect.models.settings import debug

logger = logging.getLogger(__name__)

# VoiceML serves the WAV locally only until it is uploaded to the tenant's
# own bucket or cleaned up, so the download runs in the callback itself.
RECORDING_DOWNLOAD_TIMEOUT = 30
# A dual-channel WAV of a long call stays well under this.
RECORDING_MAX_BYTES = 200 * 1024 * 1024


class Recording(models.Model):
    _inherit = 'connect.recording'

    @api.model
    @api.private
    def prepare_voicetel_data(self, rec):
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
            value = getattr(rec, field, None)
            if field == 'start_time' and value is not None and hasattr(value, 'replace'):
                value = value.replace(tzinfo=None)
            data[field] = value
        channel = self.env['connect.channel'].search(
            [('sid', '=', rec.call_sid)], limit=1
        )
        data['call'] = channel.call.id
        data['channel'] = channel.id
        return data

    @api.model
    def _voicetel_media_url(self, recording_url):
        """The RecordingUrl VoiceML reports, as an absolute URL.

        VoiceML emits Twilio's extension-less form; a relative URI is
        completed with the configured API host.
        """
        url = (recording_url or '').strip()
        if url and not url.lower().startswith('http'):
            url = 'https://{}/{}'.format(
                self.env['connect.settings']._voicetel_api_host(), url.lstrip('/'))
        return url

    @api.model
    def _voicetel_fetch_media(self, url):
        """GET a recording: trusted host only, no blind redirects, capped.

        The API key goes only to the API host (_get_media_auth). A redirect
        (to the tenant's presigned bucket) is followed once, over https,
        without credentials.
        """
        settings = self.env['connect.settings'].sudo()
        if not settings._is_trusted_media_url(url):
            raise ValueError('untrusted media URL')
        response = requests.get(
            url, auth=settings._get_media_auth(url),
            timeout=RECORDING_DOWNLOAD_TIMEOUT, stream=True,
            allow_redirects=False)
        if response.is_redirect:
            target = urljoin(url, response.headers.get('Location') or '')
            response.close()
            if not target.lower().startswith('https://'):
                raise ValueError('redirect to a non-https URL')
            response = requests.get(
                target, timeout=RECORDING_DOWNLOAD_TIMEOUT, stream=True,
                allow_redirects=False)
            if response.is_redirect:
                response.close()
                raise ValueError('more than one redirect')
        try:
            response.raise_for_status()
            declared = int(response.headers.get('Content-Length') or 0)
            if declared > RECORDING_MAX_BYTES:
                raise ValueError('recording larger than the limit')
            chunks, size = [], 0
            for chunk in response.iter_content(64 * 1024):
                size += len(chunk)
                if size > RECORDING_MAX_BYTES:
                    raise ValueError('recording larger than the limit')
                chunks.append(chunk)
            return b''.join(chunks), (response.headers.get('Content-Type') or '').lower()
        finally:
            response.close()

    @api.private
    def voicetel_download(self):
        """Pull the audio into the record while VoiceML still serves it.

        A failure is logged and the media URL is kept: the proxy then fetches
        the audio with the account credentials for as long as VoiceML keeps
        the file (or redirects to the tenant's bucket).
        """
        for rec in self:
            if rec.recording_attachment or not rec.media_url:
                continue
            try:
                content, content_type = self._voicetel_fetch_media(rec.media_url)
                if not content:
                    raise ValueError('empty recording body')
                extension = '.mp3' if 'mpeg' in content_type else '.wav'
                rec.with_context(tracking_disable=True).write({
                    'recording_attachment': base64.b64encode(content),
                    'recording_filename': '{}{}'.format(rec.sid or 'recording', extension),
                })
                debug(self, 'Downloaded VoiceML recording {} ({} bytes)'.format(
                    rec.sid, len(content)))
            except Exception as e:
                logger.warning(
                    'Could not download VoiceML recording %s: %s', rec.sid, e)
        return True

    @api.model
    @api.private
    def on_voicetel_recording_status(self, params):
        self = self.sudo()
        debug(
            self,
            'On recording status: %s' % json.dumps(params, indent=2),
        )
        if params.get('RecordingStatus') not in (None, 'completed'):
            # in-progress / absent carry no audio to store.
            return True
        data = {
            'sid': params['RecordingSid'],
            'call_sid': params['CallSid'],
            'duration': int(params.get('RecordingDuration') or 0),
            'status': params.get('RecordingStatus') or 'completed',
            'media_url': self._voicetel_media_url(params.get('RecordingUrl')),
            'source': params.get('RecordingSource'),
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
        existing = self.search([('sid', '=', data['sid'])], limit=1)
        if existing:
            existing.write(data)
            recording = existing
        else:
            recording = self.create(data)
        recording.voicetel_download()
        return True
