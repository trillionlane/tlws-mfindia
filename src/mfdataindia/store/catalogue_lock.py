"""Session lock shared only by catalogue/family publishers and factual tags.

The lock is deliberately acquired while the connection is idle and autocommit
is enabled.  The caller starts its repeatable-read transaction only after the
lock is held, so PostgreSQL cannot retain a snapshot from before serialization.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from psycopg.pq import TransactionStatus

CATALOGUE_LOCK_NAMESPACE = 1_413_423_425  # "MFDATA01"
CATALOGUE_LOCK_KEY = 2


class CataloguePublicationBusy(RuntimeError):
    """Another catalogue/family/tag operation owns the session lock."""


@contextmanager
def catalogue_publication_lock(conn: Any, *, wait: bool) -> Iterator[None]:
    """Own the catalogue session lock and always release it on this connection."""
    if conn.info.transaction_status != TransactionStatus.IDLE or not conn.autocommit:
        raise RuntimeError("catalogue lock requires an idle autocommit connection")
    function = "pg_advisory_lock" if wait else "pg_try_advisory_lock"
    row = conn.execute(
        f"SELECT {function}(%s, %s) AS acquired",
        (CATALOGUE_LOCK_NAMESPACE, CATALOGUE_LOCK_KEY),
    ).fetchone()
    acquired = True if wait else (row["acquired"] if isinstance(row, dict) else row[0])
    if acquired is not True:
        raise CataloguePublicationBusy("catalogue publication is busy")
    try:
        yield
    finally:
        row = conn.execute(
            "SELECT pg_advisory_unlock(%s, %s) AS released",
            (CATALOGUE_LOCK_NAMESPACE, CATALOGUE_LOCK_KEY),
        ).fetchone()
        released = row["released"] if isinstance(row, dict) else row[0]
        if released is not True:
            raise RuntimeError("catalogue lock was not owned by this connection")
