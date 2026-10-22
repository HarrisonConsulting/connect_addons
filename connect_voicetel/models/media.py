# -*- coding: utf-8 -*-
"""Which media URLs the server may fetch, and with which credentials.

Recording, voicemail and MMS URLs arrive in webhook parameters, so they
are untrusted input. Core's media proxy only fetches a URL a provider
claims through _is_trusted_media_url, and _get_media_auth decides whether
the API key goes with it.
"""
from urllib.parse import urlparse

from odoo import api, fields, models


class VoiceTelMediaSettings(models.Model):
    _inherit = 'connect.settings'

    voicetel_media_hosts = fields.Char(
        string='VoiceTel Media Hosts',
        groups='base.group_system',
        help='Comma-separated extra hostnames the media proxy may fetch '
             'VoiceTel recordings from (e.g. a CDN). The VoiceTel API host '
             'is always trusted. The API key is only ever sent to the API '
             'host.')

    @api.model
    def _voicetel_media_host(self, media_url):
        """Hostname of media_url if it is https without userinfo, else None."""
        try:
            parsed = urlparse(media_url or '')
            if parsed.scheme != 'https' or parsed.username or parsed.password:
                return None
            return (parsed.hostname or '').lower() or None
        except ValueError:
            return None

    @api.model
    def _voicetel_media_host_set(self):
        hosts = {self._voicetel_api_host()}
        extra = self.sudo().get_param('voicetel_media_hosts') or ''
        hosts.update(h.strip().lower() for h in extra.split(',') if h.strip())
        hosts.discard('')
        return hosts

    @api.model
    def _is_trusted_media_url(self, media_url):
        host = self._voicetel_media_host(media_url)
        if host and host in self._voicetel_media_host_set():
            return True
        return super()._is_trusted_media_url(media_url)

    @api.model
    def _get_media_auth(self, media_url):
        """VoiceML API credentials, and only for the VoiceML API host.

        Recording media is served by the API with HTTP Basic auth. Once a
        tenant's own bucket took the recording the API redirects to a
        presigned S3 URL, and the key must not go there; nor over http,
        with userinfo, or to an extra media host.
        """
        host = self._voicetel_media_host(media_url)
        if host is None:
            return None
        if host != self._voicetel_api_host():
            return super()._get_media_auth(media_url)
        settings = self.sudo()
        account_sid = settings.get_param('voicetel_account_sid')
        api_key = settings.get_param('voicetel_api_key')
        if not (account_sid and api_key):
            return super()._get_media_auth(media_url)
        return (account_sid, api_key)
