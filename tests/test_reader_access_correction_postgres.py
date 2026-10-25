"""Disposable CI-only native PG18 mechanics, not a production profile bypass.

Production run_change validates fixed production OIDs/types/ACLs. Ephemeral CI
OIDs necessarily differ: native tests exercise the same PRIVATE transaction/SQL
mechanics with explicit synthetic metadata. No test-only switch exists in the
production admission or operator, and no CI fixture grants live authority.
"""
from copy import deepcopy

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
import pytest

from mfdataindia.review import reader_access_correction as c

pytestmark = pytest.mark.postgres


@pytest.fixture
def fixture_db(pg_dsn):
    if not pg_dsn:
        pytest.skip("MF_TEST_DSN not set")
    with psycopg.connect(pg_dsn, autocommit=True) as admin:
        assert not admin.execute("SELECT 1 FROM pg_database WHERE datname='mfdataindia'").fetchone()
        fixture_roles = (c.OWNER, c.READER, "mfdata_verified_generator", "mfdata_verified_admin")
        assert not admin.execute("SELECT 1 FROM pg_roles WHERE rolname=ANY(%s)", (list(fixture_roles),)).fetchone()
        created = []
        try:
            for role in fixture_roles:
                admin.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT PASSWORD 'column-fixture-only'").format(sql.Identifier(role)))
                created.append(role)
            admin.execute("CREATE DATABASE mfdataindia")
            dsn = make_conninfo(pg_dsn, dbname="mfdataindia")
            with psycopg.connect(dsn, autocommit=True) as setup:
                setup.execute("CREATE SCHEMA mf AUTHORIZATION mfdata_admin")
                setup.execute("GRANT USAGE ON SCHEMA mf TO mfdata_verified_reader")
                cols = c.profile()["objects"][0]["all_physical_columns"]
                definitions = []
                for col in cols:
                    typ = setup.execute("SELECT format_type(%s,%s)", (col["type_oid"], col["type_modifier"])).fetchone()[0]
                    definitions.append(sql.SQL("{} {}").format(sql.Identifier(col["name"]), sql.SQL(typ)))
                setup.execute(sql.SQL("CREATE TABLE mf.facts_fixture ({})").format(sql.SQL(",").join(definitions)))
                setup.execute("INSERT INTO mf.facts_fixture(amfi_scheme_code) VALUES(154117)")
                setup.execute("ALTER TABLE mf.facts_fixture OWNER TO mfdata_admin")
                setup.execute("CREATE VIEW mf.current_fund_facts AS SELECT * FROM mf.facts_fixture")
                setup.execute("ALTER VIEW mf.current_fund_facts OWNER TO mfdata_admin")
                setup.execute("CREATE TABLE mf.fund_variants(amfi_scheme_code integer,group_key text,base_scheme_name text,regular_code integer,direct_code integer,has_direct_sibling boolean,created_at timestamptz,updated_at timestamptz)")
                setup.execute("INSERT INTO mf.fund_variants VALUES(154117,'SYNTHETIC','preserve',154117,NULL,false,'2026-01-01','2026-01-01')")
                setup.execute("ALTER TABLE mf.fund_variants OWNER TO mfdata_admin")
                setup.execute("CREATE SEQUENCE mf.fixture_sequence")
                setup.execute("CREATE FUNCTION mf.fixture_function() RETURNS int LANGUAGE SQL AS 'SELECT 1'")
                setup.execute("CREATE SCHEMA mf_review AUTHORIZATION mfdata_admin")
                for name in ("funds", "fund_facts", "nav_history", "nav_source_revisions", "dataset_summary",
                             "source_snapshot_runs", "quality_flags", "nav_quality_assessments"):
                    setup.execute(sql.SQL("CREATE TABLE mf.{}(fixture_id integer)").format(sql.Identifier(name)))
                    setup.execute(sql.SQL("ALTER TABLE mf.{} OWNER TO mfdata_admin").format(sql.Identifier(name)))
                for name in ("policies", "snapshots", "results", "events", "certifications"):
                    setup.execute(sql.SQL("CREATE TABLE mf_review.{}(fixture_id integer)").format(sql.Identifier(name)))
                    setup.execute(sql.SQL("ALTER TABLE mf_review.{} OWNER TO mfdata_admin").format(sql.Identifier(name)))
                setup.execute("CREATE FUNCTION mf_review.append_event(integer,text,text,text,text,text,jsonb) RETURNS void LANGUAGE plpgsql AS $$ BEGIN RETURN; END $$")
                setup.execute("ALTER FUNCTION mf_review.append_event(integer,text,text,text,text,text,jsonb) OWNER TO mfdata_admin")
            yield dsn
        finally:
            if admin.execute("SELECT 1 FROM pg_database WHERE datname='mfdataindia'").fetchone():
                admin.execute("DROP DATABASE mfdataindia")
            for role in reversed(created):
                admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def owner(dsn):
    return psycopg.connect(make_conninfo(dsn, user=c.OWNER, password="column-fixture-only"),
                           autocommit=True, row_factory=dict_row)


def state(dsn):
    with owner(dsn) as conn:
        return c._snapshot(conn)


def fixture_change(dsn):
    before = state(dsn)
    after, delta = c._project(before, c._selected())
    return dict(operation="GRANT", before=before, after=after, delta=delta)


def rows(dsn):
    with psycopg.connect(dsn) as conn:
        return [conn.execute("SELECT to_jsonb(t)::text FROM " + table + " t ORDER BY to_jsonb(t)::text").fetchall()
                for table in ("mf.facts_fixture", "mf.fund_variants")]


def test_native_exact_present_column_reads_denials_and_whole_preservation(fixture_db):
    plan = fixture_change(fixture_db)
    original_rows = rows(fixture_db)
    reader = make_conninfo(fixture_db, user=c.READER, password="column-fixture-only")
    with psycopg.connect(reader, autocommit=True) as conn:
        for query in ("SELECT * FROM mf.current_fund_facts", "SELECT amfi_scheme_code,group_key FROM mf.fund_variants"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(query)
    with owner(fixture_db) as conn:
        result = c._execute(conn, plan)
    assert result["stage"] == "COMMITTED_VERIFIED" and state(fixture_db) == plan["after"]
    assert rows(fixture_db) == original_rows
    assert all(not r["capabilities"][0] for r in plan["after"]["relations"] if r["relname"] in c.TARGETS)
    with psycopg.connect(reader, autocommit=True) as conn:
        assert len(conn.execute("SELECT * FROM mf.current_fund_facts").fetchone()) == 84
        assert conn.execute("SELECT amfi_scheme_code,group_key FROM mf.fund_variants").fetchone() == (154117, "SYNTHETIC")
        assert conn.execute("SELECT has_column_privilege(current_user,'mf.fund_variants','group_key','SELECT WITH GRANT OPTION')").fetchone() == (False,)
        for query in ("SELECT base_scheme_name FROM mf.fund_variants", "UPDATE mf.fund_variants SET group_key='bad'"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(query)


def test_native_future_view_column_not_silently_authorized(fixture_db):
    plan = fixture_change(fixture_db)
    with owner(fixture_db) as conn:
        c._execute(conn, plan)
        conn.execute("CREATE OR REPLACE VIEW mf.current_fund_facts AS SELECT f.*,1 AS future_column FROM mf.facts_fixture f")
    with psycopg.connect(make_conninfo(fixture_db, user=c.READER, password="column-fixture-only"), autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM mf.current_fund_facts")
        assert conn.execute("SELECT amfi_scheme_code FROM mf.current_fund_facts").fetchone() == (154117,)


def test_native_preexisting_column_grant_survives_exact_delta_reversal(fixture_db):
    with owner(fixture_db) as conn:
        conn.execute("GRANT SELECT(amfi_scheme_code) ON mf.fund_variants TO mfdata_verified_reader")
    plan = fixture_change(fixture_db)
    assert sum(len(d["columns"]) for d in plan["delta"]) == 85
    original_rows = rows(fixture_db)
    with owner(fixture_db) as conn:
        c._execute(conn, plan)
        reverse = {**deepcopy(plan), "operation": "REVOKE", "before": plan["after"], "after": plan["before"]}
        c._execute(conn, reverse)
    assert state(fixture_db) == plan["before"] and rows(fixture_db) == original_rows


def test_native_second_grant_error_rolls_back_first_no_partial_delta(fixture_db):
    plan = fixture_change(fixture_db)
    original = deepcopy(plan["before"])
    plan["delta"][1]["columns"].append("nonexistent_fixture_column")
    with owner(fixture_db) as conn, pytest.raises(c.ChangeFailure, match="PRECOMMIT_ABORTED"):
        c._execute(conn, plan)
    assert state(fixture_db) == original


@pytest.mark.parametrize("statement", [
    "GRANT SELECT(group_key) ON mf.fund_variants TO mfdata_verified_reader",
    "REVOKE SELECT(amfi_scheme_code) ON mf.fund_variants FROM mfdata_verified_reader",
    "ALTER TABLE mf.fund_variants ADD COLUMN concurrent_column integer",
    "CREATE OR REPLACE VIEW mf.current_fund_facts AS SELECT f.*,1 AS concurrent_column FROM mf.facts_fixture f",
    "DROP VIEW mf.current_fund_facts",
])
def test_native_direct_grant_revoke_and_ddl_second_admin_blocked_by_actual_locks(fixture_db, monkeypatch, statement):
    plan = fixture_change(fixture_db)
    original_snapshot = c._snapshot
    attempted = []
    with psycopg.connect(fixture_db, autocommit=True) as other:
        other.execute("SET lock_timeout='100ms'")

        def synchronized(conn):
            value = original_snapshot(conn)
            if not attempted:
                attempted.append(statement)
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    other.execute(statement)
            return value

        monkeypatch.setattr(c, "_snapshot", synchronized)
        with owner(fixture_db) as conn:
            c._execute(conn, plan)
    assert attempted == [statement]
    assert original_snapshot_connection(fixture_db, original_snapshot) == plan["after"]


def original_snapshot_connection(dsn, snapshot):
    with owner(dsn) as conn:
        return snapshot(conn)


@pytest.mark.parametrize("operation", ["GRANT SELECT ON ALL TABLES IN SCHEMA mf TO mfdata_verified_reader",
                                      "REVOKE SELECT ON ALL TABLES IN SCHEMA mf FROM mfdata_verified_reader"])
def test_native_schema_wide_admin_acl_drift_aborts_exact_delta_not_hidden_by_locks(fixture_db, monkeypatch, operation):
    # Genuine second session between snapshot capture and first GRANT. This
    # source path does not take each named relation's AccessShareLock.
    with owner(fixture_db) as conn:
        conn.execute("GRANT SELECT ON mf.facts_fixture TO mfdata_verified_reader")
    plan = fixture_change(fixture_db)
    original_snapshot = c._snapshot
    changed = []
    with psycopg.connect(fixture_db, autocommit=True) as other:
        other.execute("SET statement_timeout='2s'")

        def synchronized(conn):
            value = original_snapshot(conn)
            if not changed:
                other.execute(operation)
                changed.append(True)
            return value

        monkeypatch.setattr(c, "_snapshot", synchronized)
        with owner(fixture_db) as conn, pytest.raises(c.ChangeFailure, match="PRECOMMIT_ABORTED"):
            c._execute(conn, plan)
    assert changed == [True]
    after = original_snapshot_connection(fixture_db, original_snapshot)
    root_ids = {r["oid"] for r in after["relations"] if r["relname"] in c.TARGETS}
    assert all(not row["acl"] for row in after["columns"] if row["oid"] in root_ids)


@pytest.mark.parametrize("ddl", ["CREATE OR REPLACE VIEW mf.current_fund_facts AS SELECT f.*,1 AS new_column FROM mf.facts_fixture f",
    "DROP VIEW mf.current_fund_facts; CREATE VIEW mf.current_fund_facts AS SELECT * FROM mf.facts_fixture"])
def test_native_definition_or_oid_replacement_since_approval_aborts(fixture_db, ddl):
    plan = fixture_change(fixture_db)
    with owner(fixture_db) as conn:
        conn.execute(ddl)
        actual = c._snapshot(conn)
        with pytest.raises(c.ChangeFailure, match="PRECOMMIT_ABORTED"):
            c._execute(conn, plan)
    assert state(fixture_db) == actual


def test_native_busy_lock_nowait_aborts_without_wait_or_partial_grants(fixture_db):
    plan = fixture_change(fixture_db)
    with psycopg.connect(fixture_db) as other:
        other.execute("LOCK TABLE mf.fund_variants IN ACCESS SHARE MODE")
        with owner(fixture_db) as conn, pytest.raises(c.ChangeFailure, match="PRECOMMIT_ABORTED"):
            c._execute(conn, plan)
    assert state(fixture_db) == plan["before"]


@pytest.mark.parametrize("operation", ["ALTER ROLE mfdata_verified_reader INHERIT",
    "ALTER DEFAULT PRIVILEGES FOR ROLE mfdata_admin IN SCHEMA mf GRANT SELECT ON TABLES TO mfdata_verified_reader",
    "GRANT CREATE ON SCHEMA mf TO mfdata_verified_reader"])
def test_native_unrelated_administrative_role_default_or_schema_drift_aborts(fixture_db, monkeypatch, operation):
    plan = fixture_change(fixture_db)
    original_snapshot, changed = c._snapshot, []
    with psycopg.connect(fixture_db, autocommit=True) as other:
        def synchronized(conn):
            value = original_snapshot(conn)
            if not changed:
                other.execute(operation)
                changed.append(True)
            return value
        monkeypatch.setattr(c, "_snapshot", synchronized)
        with owner(fixture_db) as conn, pytest.raises(c.ChangeFailure, match="PRECOMMIT_ABORTED"):
            c._execute(conn, plan)
    after = original_snapshot_connection(fixture_db, original_snapshot)
    roots = {r["oid"] for r in after["relations"] if r["relname"] in c.TARGETS}
    assert changed == [True] and all(not row["acl"] for row in after["columns"] if row["oid"] in roots)


def test_native_original_server_statement_timeout_aborts_without_partial_grants(fixture_db, monkeypatch):
    plan = fixture_change(fixture_db)
    original_snapshot = c._snapshot

    def delayed(conn):
        conn.execute("SELECT pg_sleep(6)")  # Disposable CI ONLY: must hit actual 5s server bound.
        return original_snapshot(conn)

    monkeypatch.setattr(c, "_snapshot", delayed)
    with owner(fixture_db) as conn, pytest.raises(c.ChangeFailure, match="PRECOMMIT_ABORTED"):
        c._execute(conn, plan)
    assert original_snapshot_connection(fixture_db, original_snapshot) == plan["before"]


def test_native_historical_reconcile_erases_profile_and_new_admission_stops_no_reapply(fixture_db):
    from pathlib import Path
    raw = (Path(__file__).resolve().parents[1] / "scripts/sql/reconcile_verified_review_privileges.sql").read_bytes()
    plan = fixture_change(fixture_db)
    original_rows = rows(fixture_db)
    with owner(fixture_db) as conn:
        c._execute(conn, plan)
    # Deliberately exercise the UNCHANGED old bytes ONLY in this disposable CI
    # fixture; the new engine never calls the old installer/reconciler.
    with psycopg.connect(fixture_db, autocommit=True) as other:
        other.execute(raw.decode())
    after = state(fixture_db)
    with pytest.raises(c.ChangeFailure, match="PROFILE_ERASED_OR_UNAPPROVED_NO_AUTOMATIC_REAPPLY"):
        c.require_provisioning_admission(after, reconciler_bytes=raw, explicitly_selected_profile=c.PROFILE_SHA256)
    assert rows(fixture_db) == original_rows


def test_native_acl_oid_json_boundary_returns_canonical_numeric_atoms(fixture_db):
    with owner(fixture_db) as conn:
        conn.execute("GRANT SELECT(amfi_scheme_code) ON mf.fund_variants TO mfdata_verified_reader")
        # PostgreSQL's OID scalar is a JSON string unless explicitly cast.
        old_type = conn.execute("SELECT jsonb_typeof(to_jsonb('mfdata_admin'::regrole::oid)) AS kind").fetchone()["kind"]
        assert old_type == "string"
        actual = c._snapshot(conn)
    atoms = [a for group in ("relations", "columns", "namespaces", "databases", "functions", "defaults")
             for row in actual[group] for a in row["acl"]]
    assert atoms and all(type(a[0]) is int and type(a[1]) is int for a in atoms)
    plan = fixture_change(fixture_db)
    assert sum(len(d["columns"]) for d in plan["delta"]) == 85
    with owner(fixture_db) as conn:
        c._execute(conn, plan)
    assert state(fixture_db) == plan["after"]


def test_native_role_and_function_private_settings_sealed_before_client_plan_receipt_or_error(fixture_db, monkeypatch):
    import json
    sentinel = "SYNTHETIC_PRIVATE_SETTING_SENTINEL_é"
    with psycopg.connect(fixture_db, autocommit=True) as admin:
        admin.execute(sql.SQL("ALTER ROLE mfdata_admin SET vendor.api_secret TO {}").format(sql.Literal(sentinel)))
        admin.execute(sql.SQL("ALTER ROLE mfdata_verified_admin SET vendor.api_secret TO {}").format(sql.Literal(sentinel)))
        admin.execute(sql.SQL("ALTER FUNCTION mf.fixture_function() SET vendor.api_secret TO {}").format(sql.Literal(sentinel)))
    plan = fixture_change(fixture_db)
    assert sentinel not in json.dumps(plan, ensure_ascii=False)
    for group, key in (("roles", "rolconfig"), ("functions", "proconfig")):
        seals = [r[key] for r in plan["before"][group]]
        assert all(isinstance(v, dict) and set(v) == {"is_null", "sha256", "utf8_bytes"} for v in seals)
        assert any(not v["is_null"] and len(v["sha256"]) == 64 and v["utf8_bytes"] > 2 for v in seals)
        assert any(v == {"is_null": True, "sha256": None, "utf8_bytes": 0} for v in seals)
    with owner(fixture_db) as conn:
        receipt = c._execute(conn, plan)
    assert sentinel not in json.dumps(receipt, ensure_ascii=False)
    reverse = {**deepcopy(plan), "operation": "REVOKE", "before": plan["after"], "after": plan["before"]}
    with owner(fixture_db) as conn:
        c._execute(conn, reverse)

    def private_server_error(conn):
        conn.execute("DO $$ BEGIN RAISE EXCEPTION USING MESSAGE=current_setting('vendor.api_secret'); END $$")

    monkeypatch.setattr(c, "_snapshot", private_server_error)
    with owner(fixture_db) as conn, pytest.raises(c.ChangeFailure) as error:
        c._execute(conn, plan)
    assert sentinel not in str(error.value) and str(error.value) == "PRECOMMIT_ABORTED_NO_RETRY"


@pytest.mark.parametrize("kind", ["role", "function"])
def test_native_private_setting_changes_produce_different_seals_and_fail_approval_drift(fixture_db, kind):
    import json
    original = fixture_change(fixture_db)
    sentinel = "SYNTHETIC_CHANGED_PRIVATE_SETTING"
    with psycopg.connect(fixture_db, autocommit=True) as admin:
        statement = "ALTER ROLE mfdata_verified_admin SET vendor.api_secret TO {}" if kind == "role" else \
                    "ALTER FUNCTION mf.fixture_function() SET vendor.api_secret TO {}"
        admin.execute(sql.SQL(statement).format(sql.Literal(sentinel)))
    updated = state(fixture_db)
    assert c.fingerprint(updated) != c.fingerprint(original["before"])
    assert sentinel not in json.dumps(updated)
    with owner(fixture_db) as conn, pytest.raises(c.ChangeFailure, match="PRECOMMIT_ABORTED"):
        c._execute(conn, original)
    assert state(fixture_db) == updated
