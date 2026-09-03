# -*- coding: utf-8 -*-
"""Regression tests for ``connect_hc_core.tools.reraise_if_concurrency_retry``.

The Connect telephony suite relies on queue_job and Odoo's HTTP dispatcher
auto-retrying transient serialization failures (40001 / 40P01 / 55P03). A broad
``except`` that swallows such an error defeats that retry and usually launders
the retryable ``OperationalError`` into a non-retryable ``InFailedSqlTransaction``
(25P02) at the next read. This locks in the helper that keeps those errors on
the framework retry path while still letting callers swallow genuine failures.
"""

import psycopg2
from psycopg2 import errorcodes

from odoo.tests import TransactionCase, tagged

from odoo.addons.connect_hc_core.tools import reraise_if_concurrency_retry


class _FakePgError(psycopg2.OperationalError):
    """psycopg2 ``OperationalError`` test double with a settable ``pgcode``.

    Real psycopg2 errors only carry a pgcode when raised by the driver, so we
    shadow the attribute with a property to exercise the helper deterministically.
    """

    def __init__(self, pgcode, message='boom'):
        super().__init__(message)
        self._pgcode = pgcode

    @property
    def pgcode(self):
        return self._pgcode


@tagged('post_install', '-at_install')
class TestConcurrencyRetry(TransactionCase):

    def _call(self, exc):
        """Invoke the helper inside an ``except`` frame; return its result."""
        try:
            raise exc
        except Exception as e:
            return reraise_if_concurrency_retry(e)

    def test_serialization_failure_reraised(self):
        exc = _FakePgError(errorcodes.SERIALIZATION_FAILURE)
        with self.assertRaises(psycopg2.OperationalError) as cm:
            self._call(exc)
        self.assertIs(cm.exception, exc)

    def test_deadlock_reraised(self):
        with self.assertRaises(psycopg2.OperationalError):
            self._call(_FakePgError(errorcodes.DEADLOCK_DETECTED))

    def test_lock_not_available_reraised(self):
        with self.assertRaises(psycopg2.OperationalError):
            self._call(_FakePgError(errorcodes.LOCK_NOT_AVAILABLE))

    def test_non_concurrency_operationalerror_swallowed(self):
        # A non-transient OperationalError (e.g. disk full) is NOT retried here:
        # the helper returns normally so the caller can log-and-swallow.
        self.assertIsNone(self._call(_FakePgError(errorcodes.DISK_FULL)))

    def test_non_db_exception_swallowed(self):
        self.assertIsNone(self._call(ValueError('not a db error')))

    def test_wrapped_cause_reraised(self):
        # Drivers / the ORM sometimes wrap the pg error: the helper must walk
        # the __cause__ chain and still re-raise the OUTER exception unchanged.
        outer = RuntimeError('wrapped')
        outer.__cause__ = _FakePgError(errorcodes.SERIALIZATION_FAILURE)
        with self.assertRaises(RuntimeError) as cm:
            self._call(outer)
        self.assertIs(cm.exception, outer)

    def test_wrapped_context_reraised(self):
        outer = RuntimeError('wrapped via context')
        outer.__context__ = _FakePgError(errorcodes.SERIALIZATION_FAILURE)
        with self.assertRaises(RuntimeError):
            self._call(outer)

    def test_wrapped_non_concurrency_swallowed(self):
        outer = RuntimeError('wrapped non-retryable')
        outer.__cause__ = _FakePgError(errorcodes.DISK_FULL)
        self.assertIsNone(self._call(outer))
