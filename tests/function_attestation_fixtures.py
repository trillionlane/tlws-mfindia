"""Synthetic exact-profile recorder only. Never a connection factory."""
from copy import deepcopy
from types import SimpleNamespace

from psycopg.pq import TransactionStatus

from mfdataindia.review import reader_function_attestation_inventory as inv
from test_reader_function_attestation_validation import fixture as attestation_document


def context():
    return {
        "database": "mfdataindia", "principal": "mfdata_admin", "session": "mfdata_admin",
        "version": 180006, "isolation": "repeatable read", "readonly": "on",
        "statement_timeout": "5s", "lock_timeout": "5s", "transaction_timeout": "15s",
        "search_path": "pg_catalog", "timezone": "UTC", "pid": 1234, "snapshot": "12:12:",
        "transaction_started_at": "2026-10-28T00:00:00.000000Z",
    }


class Cursor:
    def __init__(self, rows, columns=(), tag=None):
        self.rows = deepcopy(rows)
        self.description = [SimpleNamespace(name=c) for c in columns]
        self.rowcount = len(rows)
        self.statusmessage = tag or "SELECT " + str(len(rows))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def fetchall(self):
        return deepcopy(self.rows)


class Connection:
    autocommit = True

    def __init__(self):
        self.info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)
        self.calls = []
        self.closed = 0
        self.doc = attestation_document()
        self.ctx = context()
        self.context_reads = self.document_reads = 0
        self.execute_hook = None

    def execute(self, statement, *, prepare=False):
        assert prepare is False
        self.calls.append(statement)
        if self.execute_hook:
            value = self.execute_hook(statement)
            if value is not None:
                return value
        if statement in inv.CONTROLS:
            return Cursor([], tag="BEGIN" if statement.startswith("BEGIN") else "SET")
        if statement == inv.TERMINAL:
            return Cursor([], tag="ROLLBACK")
        if statement == inv.CONTEXT_SQL:
            self.context_reads += 1
            return Cursor([self.ctx], inv.CONTEXT_COLUMNS)
        if statement == inv.ATTESTATION_SQL:
            self.document_reads += 1
            return Cursor([{"value": self.doc}], ["value"])
        raise AssertionError("caller SQL is never accepted")

    def close(self):
        self.closed += 1
