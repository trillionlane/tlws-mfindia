"""Disposable CI PG18 fixed SQL mechanics, never a production identity bypass."""

from copy import deepcopy

import psycopg
import pytest

from mfdataindia.review import reader_permission_capture as c
from mfdataindia.review import reader_permission_capture_inventory as inv
from mfdataindia.review import reader_permission_capture_spec as spec
from mfdataindia.review.reader_access_owner_connection import LOCK_WITNESS_SQL
from test_reader_access_correction_postgres import fixture_db as fixture_db
from test_reader_access_correction_postgres import owner, state, rows

pytestmark = pytest.mark.postgres


def capture(dsn):
    with owner(dsn) as conn, c._capture(conn) as value:
        return deepcopy(value)  # PRIVATE fixed mechanics only; not public production admission.


def actual_locks(dsn):
    with owner(dsn) as conn:
        conn.execute("BEGIN")
        try:
            conn.execute("LOCK TABLE ONLY mf.current_fund_facts IN ACCESS EXCLUSIVE MODE NOWAIT")
            conn.execute("LOCK TABLE ONLY mf.fund_variants IN ACCESS EXCLUSIVE MODE NOWAIT")
            return [dict(r) for r in conn.execute(LOCK_WITNESS_SQL).fetchall()]
        finally:
            conn.execute("ROLLBACK")


def test_native_complete_snapshot_rollback_preserves_all_rows_acls_and_private_seals(fixture_db):
    before, original = state(fixture_db), rows(fixture_db)
    value = capture(fixture_db)
    assert value["state"] == before == state(fixture_db) and original == rows(fixture_db)
    spec.context_shape(value["context"])
    spec.state_shape(value["state"])
    assert value["context"]["readonly"] == "on" and len(value["completions"]) == 6
    assert value["candidate"] == actual_locks(fixture_db)


@pytest.mark.parametrize("setup", ["base", "nested", "inherited", "only"])
def test_native_view_and_inheritance_candidate_has_explicit_conservative_only_limit(
    fixture_db, setup
):
    with psycopg.connect(fixture_db, autocommit=True) as admin:
        if setup == "nested":
            admin.execute("CREATE VIEW mf.nested_capture AS SELECT * FROM mf.facts_fixture")
            admin.execute("ALTER VIEW mf.nested_capture OWNER TO mfdata_admin")
            admin.execute(
                "CREATE OR REPLACE VIEW mf.current_fund_facts AS SELECT * FROM mf.nested_capture"
            )
        elif setup in ("inherited", "only"):
            admin.execute("CREATE TABLE mf.capture_child () INHERITS(mf.facts_fixture)")
            admin.execute("ALTER TABLE mf.capture_child OWNER TO mfdata_admin")
            if setup == "only":
                admin.execute(
                    "CREATE OR REPLACE VIEW mf.current_fund_facts AS SELECT * FROM ONLY mf.facts_fixture"
                )
    value, actual = capture(fixture_db), actual_locks(fixture_db)
    assert all(r in value["candidate"] for r in actual)
    if setup == "only":
        assert value["candidate"] != actual  # Cannot claim pg_depend is an exact lock witness.
        assert "capture_child" in {r["relname"] for r in value["candidate"]}
        assert "capture_child" not in {r["relname"] for r in actual}
    else:
        assert value["candidate"] == actual


def test_native_candidate_rejects_outside_scope_without_omission(fixture_db):
    with psycopg.connect(fixture_db, autocommit=True) as admin:
        admin.execute("CREATE TABLE public.outside_capture(x int)")
        admin.execute("ALTER TABLE public.outside_capture OWNER TO mfdata_admin")
        admin.execute(
            "CREATE OR REPLACE VIEW mf.current_fund_facts AS SELECT f.* FROM mf.facts_fixture f CROSS JOIN public.outside_capture"
        )
    with pytest.raises(ValueError):
        capture(fixture_db)
    assert state(fixture_db)["relations"]


def test_native_readonly_capture_does_not_request_recursive_access_exclusive_locks(fixture_db):
    with owner(fixture_db) as writer:
        writer.execute("BEGIN")
        try:
            writer.execute("UPDATE mf.facts_fixture SET amfi_scheme_code=154118")
            value = capture(
                fixture_db
            )  # Writer RowExclusive lock does not block catalogue SELECTs.
            assert value["context"]["readonly"] == "on"
        finally:
            writer.execute("ROLLBACK")
    assert rows(fixture_db)


def test_native_private_role_function_settings_are_sealed_on_server_not_connection(fixture_db):
    with psycopg.connect(fixture_db, autocommit=True) as admin:
        admin.execute(
            "ALTER ROLE mfdata_verified_reader SET private.capture_password='fixture-secret-marker'"
        )
        admin.execute(
            "ALTER FUNCTION mf.fixture_function() SET private.capture_password='fixture-function-marker'"
        )
    value = capture(fixture_db)
    raw = spec.canonical(value)
    assert "fixture-secret-marker" not in raw and "fixture-function-marker" not in raw
    role = next(r for r in value["state"]["roles"] if r["rolname"] == "mfdata_verified_reader")
    assert role["rolconfig"]["is_null"] is False and role["rolconfig"]["utf8_bytes"] > 0
    assert value["state"] == state(fixture_db)


def test_native_public_production_profile_rejects_ephemeral_fixture_identity(fixture_db):
    with owner(fixture_db) as conn, pytest.raises(inv.engine.ChangeFailure):
        c.Capture().run(conn)
    assert state(fixture_db)
