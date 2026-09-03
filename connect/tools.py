"""Shared helpers for the Connect telephony suite."""

import logging

from twilio.request_validator import RequestValidator


_logger = logging.getLogger(__name__)


def validate_twilio_request(settings, httprequest, data, *, region=False):
    """Validate a Twilio request and fail closed on every missing precondition.

    The public webhook controllers all share this policy.  Keeping it here
    prevents a newly added integration from interpreting a disabled setting or
    missing credential as permission to accept an unsigned request.

    ``region=True`` preserves Connect's regional-auth-token behavior.  Other
    Twilio callbacks use the account auth token, matching their existing
    validation contract.
    """
    if not settings.sudo().get_param('twilio_verify_requests'):
        _logger.critical(
            'SECURITY: Twilio webhook signature verification is disabled; '
            'rejecting the request.'
        )
        return False

    _, auth_token = settings.sudo()._get_client_credentials()
    if region and settings.sudo().get_param('rest_provider') == 'twilio':
        auth_token = settings.sudo().get_param('region_auth_token') or auth_token
    if not auth_token:
        _logger.critical(
            'SECURITY: Twilio webhook signature verification has no auth token; '
            'rejecting the request.'
        )
        return False

    url = httprequest.url.replace('http:', 'https:', 1)
    signature = httprequest.headers.get('X-Twilio-Signature', '')
    try:
        request_valid = RequestValidator(auth_token).validate(url, data, signature)
    except Exception:  # malformed input and validator errors must fail closed
        _logger.exception(
            'Twilio signature validation failed unexpectedly for %s',
            httprequest.path,
        )
        return False

    if not request_valid:
        if httprequest.url.startswith('http:'):
            _logger.error('Twilio requires HTTPS to be configured.')
        else:
            _logger.error(
                'Twilio request signature is invalid for %s; '
                'signature_header_present=%s signature_len=%s',
                httprequest.path,
                bool(signature),
                len(signature),
            )
    return request_valid
