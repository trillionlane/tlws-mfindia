"""Fresh disposable PG18 source compilation; no live grant or function call."""

import json
from pathlib import Path
import time

import psycopg
from psycopg.rows import dict_row
import pytest

from mfdataindia.review import broad_reader_function_closure as closure
from mfdataindia.review import broad_reader_policy as policy
from mfdataindia.review import broad_reader_capture_inventory as capture_inventory
from mfdataindia.review import broad_reader_dependency_scope as dependencies
from mfdataindia.review import broad_reader_view_profile as view_profile
from mfdataindia.review import broad_reader_lock_witness as lock_witness
from mfdataindia.review import broad_reader_source_authority as source_authority
from mfdataindia.review import broad_reader_direct_reader_check as reader_check


pytestmark = pytest.mark.postgres
pytest_plugins = ("test_reader_source_reference_postgres",)


def test_native_complete_source_helper_callee_seals_and_acl(source_reference_db):
    dsn, _ = source_reference_db
    packaged = source_authority.verify_packaged_source(Path(__file__).resolve().parents[1])
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY", prepare=False)
        for setting in ("statement_timeout='5s'", "lock_timeout='5s'",
                        "transaction_timeout='15s'", "search_path=pg_catalog",
                        "TimeZone='UTC'"):
            conn.execute("SET LOCAL " + setting, prepare=False)
        state = conn.execute(policy.STATE_SQL, prepare=False).fetchone()["value"]
        ledger = [dict(row) for row in conn.execute(source_authority.LEDGER_SQL,
                                                   prepare=False).fetchall()]
        assert source_authority.verify_live_ledger(ledger, packaged)["rows"] == 44
        candidates = [dict(row) for row in conn.execute(capture_inventory.CANDIDATE_SQL,
                                                        prepare=False).fetchall()]
        leaves = [dict(row) for row in conn.execute(dependencies.SYSTEM_LEAVES_SQL,
                                                   prepare=False).fetchall()]
        leaf_by_name = {row["relname"]: row for row in leaves}
        assert set(leaf_by_name) == dependencies.SYSTEM_LEAF_NAMES
        scope = dependencies.classify(state, candidates + leaves,
                                      system_leaves=leaf_by_name)
        with conn.execute(closure.FUNCTION_SQL, prepare=False) as function_cursor:
            assert [column.name for column in function_cursor.description] == closure.FUNCTION_COLUMNS
            rows = [dict(row) for row in function_cursor.fetchall()]
        proof = closure.verify(rows, state)
        selected = view_profile.select(
            state, candidates,
            base_authority_sha256=view_profile.BASE_AUTHORITY_SHA256,
            variant_authority_sha256=view_profile.VARIANT_AUTHORITY_SHA256)
        assert proof["function_invoked"] is False
        assert proof["grant_authorized"] is False
        assert len(proof["functions"]) == 2
        assert selected["profile"] == "BASE44_EXTENDED"
        assert len(scope["lock_only_leaves"]) == 3
        assert all("pg_catalog" not in statement
                   for statement in dependencies.lock_statements(scope))
        # Compile the later direct-reader metadata program on the same fresh
        # source DB. This owner-side syntax check is NOT reader acceptance.
        for statement, names in (
            (reader_check.CONTEXT_SQL, ["database", "principal", "session",
                                        "in_recovery", "readonly", "isolation"]),
            (reader_check.SCHEMA_SQL, ["oid", "nspname", "usage", "create_allowed"]),
            (reader_check.RELATION_SQL, ["oid", "nspname", "relname", "relkind",
                                         "select_allowed", "insert_allowed",
                                         "update_allowed", "delete_allowed",
                                         "truncate_allowed", "references_allowed",
                                         "trigger_allowed"]),
            (reader_check.COLUMN_SQL, ["oid", "attnum", "attname", "select_allowed",
                                       "insert_allowed", "update_allowed",
                                       "references_allowed"]),
        ):
            with conn.execute(statement, prepare=False) as cursor:
                assert [column.name for column in cursor.description] == names
                assert cursor.statusmessage.startswith("SELECT ")
        conn.execute("ROLLBACK", prepare=False)
        # Same disposable source DB, but a separate target backend. This is
        # source-only capacity calibration, not the later live grant receipt.
        with psycopg.connect(dsn, autocommit=True, row_factory=dict_row,
                             connect_timeout=5) as target:
            started = time.monotonic()
            target.execute("BEGIN ISOLATION LEVEL READ COMMITTED READ ONLY", prepare=False)
            context = target.execute(
                "SELECT pg_backend_pid()::bigint AS pid,"
                "(SELECT oid::bigint FROM pg_catalog.pg_database "
                "WHERE datname=current_database()) AS database", prepare=False).fetchone()
            target.execute("ROLLBACK", prepare=False)
            observer_pid = conn.info.backend_pid
            assert observer_pid != context["pid"]

            def snapshot():
                return [dict(row) for row in conn.execute(
                    lock_witness.LOCK_WITNESS_SQL, (context["pid"],),
                    prepare=False).fetchall()]

            baseline = snapshot()
            target.execute("BEGIN ISOLATION LEVEL READ COMMITTED READ WRITE", prepare=False)
            for setting in ("statement_timeout='5s'", "lock_timeout='5s'",
                            "transaction_timeout='15s'", "search_path=pg_catalog",
                            "TimeZone='UTC'"):
                target.execute("SET LOCAL " + setting, prepare=False)
            table_sql, view_sql = dependencies.lock_phases(scope)
            for statement in table_sql:
                target.execute(statement, prepare=False)
            table_locks = snapshot()
            for statement in view_sql:
                target.execute(statement, prepare=False)
            view_locks = snapshot()
            proof = lock_witness.verify_phases(
                scope, baseline=baseline, tables=table_locks, views=view_locks,
                target_pid=context["pid"], observer_pid=observer_pid,
                database_oid=context["database"])
            assert proof["grant_authorized"] is False
            assert len(json.dumps(proof, sort_keys=True).encode()) < 8 * 1024 * 1024
            # The observer's complete prewrite reread runs while the target
            # holds every lock. It must fit the SAME 15-second wall, and no
            # target catalogue query or GRANT is introduced by this test.
            state_again = conn.execute(policy.STATE_SQL, prepare=False).fetchone()["value"]
            candidates_again = [dict(row) for row in conn.execute(
                capture_inventory.CANDIDATE_SQL, prepare=False).fetchall()]
            functions_again = [dict(row) for row in conn.execute(
                closure.FUNCTION_SQL, prepare=False).fetchall()]
            ledger_again = [dict(row) for row in conn.execute(
                source_authority.LEDGER_SQL, prepare=False).fetchall()]
            assert state_again == state
            assert candidates_again == candidates
            assert functions_again == rows and ledger_again == ledger
            assert len(json.dumps({"state": state_again, "candidates": candidates_again,
                                   "functions": functions_again, "ledger": ledger_again,
                                   "lock_witness": proof}, sort_keys=True).encode()) < 8 * 1024 * 1024
            assert time.monotonic() - started < 15
            target.execute("ROLLBACK", prepare=False)
            assert snapshot() == []
