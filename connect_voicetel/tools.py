# -*- coding: utf-8 -*-
"""Hostname rules shared by the settings constraints and the migration.

No ORM here: the 19.0.4.0.0 pre-migration imports this before the
module's models are loaded.
"""
from urllib.parse import urlsplit

DEFAULT_VOICETEL_API_HOST = 'voiceml.voicetel.com'
DEFAULT_VOICETEL_SIP_SUFFIX = 'vml.voice.tel'
# The API key is sent to the API host and the SIP registrar only
# authenticates under the suffix, so both are held to VoiceTel's own zones
# unless connect.voicetel_allow_custom_host is set.
API_HOST_ZONE = 'voicetel.com'
SIP_SUFFIX_ZONE = 'voice.tel'


def normalize_host(value):
    """Bare lowercase hostname of ``value``: no scheme, userinfo, port or path.

    Returns '' when nothing usable is left or when the value carries
    userinfo (credentials in a host setting are refused, not stripped).
    """
    value = (value or '').strip()
    if not value:
        return ''
    if '//' not in value:
        value = '//' + value
    try:
        parts = urlsplit(value)
        if parts.username or parts.password:
            return ''
        host = (parts.hostname or '').strip('.').lower()
    except ValueError:
        return ''
    return host


def in_zone(host, zone):
    """True when ``host`` is ``zone`` or a name under it."""
    host = (host or '').lower()
    return bool(host) and (host == zone or host.endswith('.' + zone))
