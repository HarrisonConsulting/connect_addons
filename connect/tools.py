"""Shared helpers for the Connect telephony suite."""

import psycopg2

from odoo.service.model import PG_CONCURRENCY_ERRORS_TO_RETRY


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
    """
    cause = exc
    seen = set()
    while cause is not None and id(cause) not in seen:
        if (isinstance(cause, psycopg2.OperationalError)
                and cause.pgcode in PG_CONCURRENCY_ERRORS_TO_RETRY):
            raise exc
        seen.add(id(cause))
        cause = cause.__cause__ or cause.__context__
