"""Database-operation locks shared by ingestion and derived-data jobs.

The NAV refresh and NAV-quality audit touch different tables, but the audit
must describe one fully committed dataset version.  A transaction-scoped
PostgreSQL advisory lock makes an overlap fail closed without leaving a
session lock behind after commit, rollback, or connection loss.
"""

from __future__ import annotations

from typing import Any

# Two signed 32-bit keys use PostgreSQL's two-key advisory-lock namespace.
# They are fixed protocol constants rather than hashes computed by PostgreSQL,
# so their meaning cannot change with database collation or implementation.
NAV_DATA_OPERATION_LOCK_NAMESPACE = 1_413_423_425  # "MFDATA01"
NAV_DATA_OPERATION_LOCK_KEY = 1


class DataOperationBusy(RuntimeError):
    """Raised when another governed NAV data operation owns the lock."""


def acquire_nav_data_operation_lock(conn: Any) -> None:
    """Acquire the shared NAV-operation lock for the current transaction.

    The function deliberately uses ``pg_try_advisory_xact_lock``.  Scheduled
    work must not wait behind an unexpectedly long refresh or audit and then
    run against a dataset version different from the one the operator saw.
    PostgreSQL releases the lock automatically with the transaction.
    """

    row = conn.execute(
        "SELECT pg_try_advisory_xact_lock(%s, %s) AS acquired",
        (NAV_DATA_OPERATION_LOCK_NAMESPACE, NAV_DATA_OPERATION_LOCK_KEY),
    ).fetchone()
    acquired = row["acquired"] if isinstance(row, dict) else row[0]
    if acquired is not True:
        raise DataOperationBusy(
            "another governed NAV refresh or quality audit is already running"
        )
