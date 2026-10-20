# -*- coding: utf-8 -*-
from urllib.parse import urlparse

from odoo import api, fields, models


class VoiceMLMediaSettings(models.Model):
    _inherit = 'connect.settings'

    voiceml_media_hosts = fields.Char(
        string='VoiceML Media Hosts',
        help='Comma-separated extra hostnames the media proxy may fetch '
        'VoiceML recordings from (e.g. a CDN). The VoiceML API host is '
        'always trusted.')

    @api.model
    def _voiceml_media_host_set(self):
        hosts = {self._voiceml_api_host()}
        extra = self.sudo().get_param('voiceml_media_hosts') or ''
        hosts.update(h.strip().lower() for h in extra.split(',') if h.strip())
        hosts.discard('')
        return hosts

    @api.model
    def _voiceml_media_host(self, media_url):
        """Hostname of media_url if it is https without userinfo, else None."""
        try:
            parsed = urlparse(media_url or '')
            if parsed.scheme != 'https' or parsed.username or parsed.password:
                return None
            return (parsed.hostname or '').lower() or None
        except ValueError:
            return None

    @api.model
    def _is_trusted_media_url(self, media_url):
        host = self._voiceml_media_host(media_url)
        if host and host in self._voiceml_media_host_set():
            return True
        return super()._is_trusted_media_url(media_url)

    @api.model
    def _get_media_auth(self, media_url):
        host = self._voiceml_media_host(media_url)
        if not host or host not in self._voiceml_media_host_set():
            # Never send the API key over http, with userinfo, or off-host.
            return None
        return super()._get_media_auth(media_url)
