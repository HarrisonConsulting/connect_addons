"""Shared helpers for the Connect telephony suite."""

import logging

import psycopg2
from twilio.request_validator import RequestValidator

from odoo.service.model import (
    PG_CONCURRENCY_ERRORS_TO_RETRY,
    PG_CONCURRENCY_EXCEPTIONS_TO_RETRY,
)


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


def reraise_if_concurrency_retry(exc):
    """Re-raise transient transaction-serialization errors so the framework
    can auto-retry the unit of work.

    Both queue_job (OCA) and Odoo's HTTP dispatcher (``service.model.retrying``)
    automatically retry a job/request that raises a ``psycopg2.OperationalError``
    whose pgcode is a transient concurrency error — serialization_failure
    (40001), deadlock_detected (40P01) or lock_not_available (55P03). A broad
    ``except Exception`` that logs-and-swallows such an error DEFEATS that retry.
    Worse: because ``_FlushingSavepoint`` flushes the cursor BEFORE it issues
    its ``SAVEPOINT`` (odoo/sql_db.py), a swallowed serialization failure usually
    leaves the transaction aborted, so the next ORM read re-raises it as a
    NON-retryable ``InFailedSqlTransaction`` (25P02) and the whole unit fails.

    Call this as the FIRST statement of any broad ``except`` that wraps ORM work
    in queue-job / cron / webhook context: it re-raises the original exception
    (traceback preserved) when it is a retryable concurrency error — walking the
    ``__cause__`` / ``__context__`` chain so a wrapped error is still caught —
    and returns normally otherwise, leaving the caller free to log-and-swallow
    genuine failures.

    Matching is by exception CLASS first, mirroring Odoo core
    (``service/model.py`` ``retrying()`` tests
    ``isinstance(exc, PG_CONCURRENCY_EXCEPTIONS_TO_RETRY)`` before it ever looks
    at a pgcode). ``psycopg2.Error.pgcode`` is a READ-ONLY attribute populated
    from the live cursor, so a concurrency exception that is re-created rather
    than raised by the driver carries ``pgcode = None``. Matching on pgcode alone
    silently failed to re-raise those, which is the precise bug this helper
    exists to prevent. The pgcode test is kept as a second arm so an exotic
    ``OperationalError`` carrying a concurrency pgcode but not one of the three
    classes is still caught; the two arms together are a strict superset of the
    old behaviour, so this can only ever re-raise more, never less.
    """
    cause = exc
    seen = set()
    while cause is not None and id(cause) not in seen:
        if (isinstance(cause, PG_CONCURRENCY_EXCEPTIONS_TO_RETRY)
                or (isinstance(cause, psycopg2.OperationalError)
                    and cause.pgcode in PG_CONCURRENCY_ERRORS_TO_RETRY)):
            raise exc
        seen.add(id(cause))
        cause = cause.__cause__ or cause.__context__
