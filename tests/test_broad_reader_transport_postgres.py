"""Disposable native PG18 only; no local/DEV/PROD connection authority."""

from copy import deepcopy
import hashlib

import psycopg
from psycopg import sql
import pytest

from mfdataindia.review import broad_reader_policy as policy
from mfdataindia.review import broad_reader_capture as capture
from mfdataindia.review import broad_reader_capture_spec as cs
from mfdataindia.review import broad_reader_capture_controller as cc
from mfdataindia.review import broad_reader_transport_spec as spec
from mfdataindia.review import broad_reader_owner_connection as gate
from mfdataindia.review.rollout import Manifest, seal_evidence
from test_reader_access_correction_postgres import fixture_db as fixture_db
from test_reader_access_correction_postgres import owner
from test_broad_reader_policy_postgres import reader
from broad_reader_fixtures import capture_completion, window

pytestmark = pytest.mark.postgres


@pytest.fixture
def broad_db(fixture_db):
    with psycopg.connect(fixture_db, autocommit=True) as admin:
        for name in policy.SCHEMAS:
            if name not in ("mf", "mf_review"):
                admin.execute(
                    sql.SQL("CREATE SCHEMA {} AUTHORIZATION mfdata_admin").format(
                        sql.Identifier(name)
                    )
                )
    with owner(fixture_db) as conn:
        for name in policy.SCHEMAS:
            conn.execute(
                sql.SQL("CREATE TABLE {}.broad_fixture(value integer)").format(sql.Identifier(name))
            )
            conn.execute(
                sql.SQL("INSERT INTO {}.broad_fixture VALUES(7)").format(sql.Identifier(name))
            )
        conn.execute(
            "CREATE MATERIALIZED VIEW mf.broad_materialized AS SELECT value FROM mf.broad_fixture"
        )
    return fixture_db


def read_capture(dsn):
    with owner(dsn) as conn:
        return capture.Capture().run(conn)


def state(dsn):
    with owner(dsn) as conn:
        return conn.execute(policy.STATE_SQL).fetchone()["value"]


def all_rows(dsn):
    result = {}
    before = state(dsn)
    with owner(dsn) as conn:
        for row in before["relations"]:
            if row["relkind"] in ("r", "p", "v", "m"):
                name = (row["nspname"], row["relname"])
                result[name] = conn.execute(
                    sql.SQL("SELECT * FROM {}.{}").format(
                        sql.Identifier(name[0]), sql.Identifier(name[1])
                    )
                ).fetchall()
    return result


def proposal(dsn):
    c = capture_completion()
    m = Manifest.model_validate(c["approval"]["manifest"])
    body = read_capture(dsn)
    body["worker_binding"] = deepcopy(c["certificate"]["evidence"]["worker_binding"])
    body["input_binding"] = deepcopy(c["certificate"]["evidence"]["input_binding"])
    c["certificate"] = seal_evidence(m, cc.STAGE, body)
    cc.validate_completion(c)  # Synthetic provider package, actual disposable SQL body.
    return spec.proposal(c, window(c))


class Recorder:
    def __init__(self, conn):
        self.conn = conn
        self.calls = []

    @property
    def autocommit(self):
        return self.conn.autocommit

    @property
    def info(self):
        return self.conn.info

    def execute(self, statement, *, prepare=False):
        self.calls.append(statement if type(statement) is str else statement.as_string())
        return self.conn.execute(statement, prepare=prepare)


def apply(dsn, p):
    document = spec.engine_document(p)
    with owner(dsn) as conn:
        wrapped = Recorder(conn)
        result = policy.apply(
            gate.guarded_connection(wrapped, p),
            policy.Permit(document, policy.fingerprint(document)),
        )
        return result, wrapped.calls


def test_native_public_complete_broad_capture_preserves_all_sixteen_rows_acls_and_m(broad_db):
    before, rows = state(broad_db), all_rows(broad_db)
    value = read_capture(broad_db)
    assert value["state"] == before == state(broad_db)
    assert all_rows(broad_db) == rows
    assert {r["nspname"] for r in value["candidate"]} == set(policy.SCHEMAS)
    materialized = next(r for r in value["candidate"] if r["relkind"] == "m")
    with owner(broad_db) as conn:
        definition = (
            conn.execute("SELECT pg_get_viewdef('mf.broad_materialized'::regclass,false) AS value")
            .fetchone()["value"]
            .encode()
        )
    assert materialized["definition"] == dict(
        is_null=False, sha256=hashlib.sha256(definition).hexdigest(), utf8_bytes=len(definition)
    )
    assert next(r for r in value["state"]["relations"] if r["oid"] == materialized["oid"])[
        "definition"
    ] == dict(is_null=True, sha256=None, utf8_bytes=0)
    assert len(value["completions"]) == 6 and value["context"]["readonly"] == "on"


def test_native_full_sixteen_delta_actual_witness_materialized_read_and_all_rows_preserved(
    broad_db,
):
    before, rows = state(broad_db), all_rows(broad_db)
    p = proposal(broad_db)
    result, calls = apply(broad_db, p)
    assert result["stage"] == "COMMITTED_VERIFIED"
    assert policy.catalogue(state(broad_db)) == p["change"]["after"]
    assert all_rows(broad_db) == rows
    assert policy.prepare(before) == p["change"]
    assert not any(s.startswith("LOCK") and "broad_materialized" in s for s in calls)
    assert p["materialized_unlocked"] and all(
        r["relkind"] == "m" for r in p["materialized_unlocked"]
    )
    with reader(broad_db) as conn:
        for name in policy.SCHEMAS:
            assert conn.execute(
                sql.SQL("SELECT value FROM {}.broad_fixture").format(sql.Identifier(name))
            ).fetchone() == {"value": 7}
        assert conn.execute("SELECT value FROM mf.broad_materialized").fetchone() == {"value": 7}
        for command in (
            "UPDATE mf.broad_fixture SET value=0",
            "DELETE FROM mf.broad_fixture",
            "CREATE TABLE mf.not_allowed(x int)",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(command)


@pytest.mark.parametrize("schema", ["mf", "mf_review", "mf_analytics", "mf_rolling"])
def test_native_future_owner_objects_and_columns_follow_bound_schema_local_defaults(
    broad_db, schema
):
    apply(broad_db, proposal(broad_db))
    with owner(broad_db) as conn:
        conn.execute(
            sql.SQL("CREATE TABLE {}.future_broad AS SELECT 9 AS value").format(
                sql.Identifier(schema)
            )
        )
        conn.execute(
            sql.SQL(
                "ALTER TABLE {}.broad_fixture ADD COLUMN future_value integer DEFAULT 8"
            ).format(sql.Identifier(schema))
        )
    with reader(broad_db) as conn:
        assert conn.execute(
            sql.SQL("SELECT value FROM {}.future_broad").format(sql.Identifier(schema))
        ).fetchone() == {"value": 9}
        assert conn.execute(
            sql.SQL("SELECT future_value FROM {}.broad_fixture").format(sql.Identifier(schema))
        ).fetchone() == {"future_value": 8}
    assert policy.require_current_policy(state(broad_db))["present_schemas"] == sorted(
        policy.SCHEMAS
    )


def test_native_outside_materialized_dependency_is_visible_not_filtered(broad_db):
    with psycopg.connect(broad_db, autocommit=True) as admin:
        admin.execute("CREATE MATERIALIZED VIEW public.outside_broad AS SELECT 1 AS value")
        admin.execute("ALTER MATERIALIZED VIEW public.outside_broad OWNER TO mfdata_admin")
    with owner(broad_db) as conn:
        conn.execute("CREATE VIEW mf.outside_broad_view AS SELECT value FROM public.outside_broad")
    before, rows = state(broad_db), all_rows(broad_db)
    with pytest.raises(ValueError):
        read_capture(broad_db)
    assert state(broad_db) == before and all_rows(broad_db) == rows


def test_native_materialized_root_outside_dependency_is_visible_not_filtered(broad_db):
    with psycopg.connect(broad_db, autocommit=True) as admin:
        admin.execute("CREATE TABLE public.outside_materialized_source(value integer)")
        admin.execute("ALTER TABLE public.outside_materialized_source OWNER TO mfdata_admin")
    with owner(broad_db) as conn:
        conn.execute(
            "CREATE MATERIALIZED VIEW mf.outside_materialized_root AS SELECT value FROM public.outside_materialized_source"
        )
    before, rows = state(broad_db), all_rows(broad_db)
    with pytest.raises(ValueError):
        read_capture(broad_db)
    assert state(broad_db) == before and all_rows(broad_db) == rows


@pytest.mark.parametrize("kind", ["partition", "inheritance"])
def test_native_root_outside_child_is_visible_not_filtered(broad_db, kind):
    with psycopg.connect(broad_db, autocommit=True) as admin:
        if kind == "partition":
            admin.execute(
                "CREATE TABLE mf.outside_child_root(value integer) PARTITION BY RANGE(value)"
            )
            admin.execute(
                "CREATE TABLE public.outside_child PARTITION OF mf.outside_child_root FOR VALUES FROM (0) TO (10)"
            )
        else:
            admin.execute("CREATE TABLE mf.outside_child_root(value integer)")
            admin.execute("CREATE TABLE public.outside_child() INHERITS(mf.outside_child_root)")
        admin.execute("ALTER TABLE mf.outside_child_root OWNER TO mfdata_admin")
        admin.execute("ALTER TABLE public.outside_child OWNER TO mfdata_admin")
        admin.execute("INSERT INTO public.outside_child VALUES(7)")
    before, rows = state(broad_db), all_rows(broad_db)
    with pytest.raises(ValueError):
        read_capture(broad_db)
    assert state(broad_db) == before and all_rows(broad_db) == rows


def test_native_unapproved_actual_lock_witness_aborts_before_grant_and_keeps_full_state(broad_db):
    p = proposal(broad_db)
    before, rows = state(broad_db), all_rows(broad_db)
    wanted = deepcopy(p)

    # Use real approved public proposal. Replace native pg_locks query result through
    # a recording connection only, to exercise denial without production overrides.
    class Drift(Recorder):
        def execute(self, statement, *, prepare=False):
            from mfdataindia.review.reader_access_owner_connection import LOCK_WITNESS_SQL

            cursor = super().execute(statement, prepare=prepare)
            if statement == LOCK_WITNESS_SQL:

                class Wrong:
                    def __enter__(self):
                        return self

                    def __exit__(self, *_):
                        cursor.close()

                    description = cursor.description
                    rowcount = 0
                    statusmessage = "SELECT 0"

                    def fetchall(self):
                        return []

                return Wrong()
            return cursor

    document = spec.engine_document(wanted)
    with owner(broad_db) as conn:
        wrapped = Drift(conn)
        with pytest.raises(policy.PolicyError, match="PRECOMMIT_ABORTED"):
            policy.apply(
                gate.guarded_connection(wrapped, wanted),
                policy.Permit(document, policy.fingerprint(document)),
            )
        assert not any(s in wanted["change"]["sql"] for s in wrapped.calls)
        assert wrapped.calls[-1] == "ROLLBACK"
    assert state(broad_db) == before and all_rows(broad_db) == rows


def test_native_concurrent_writer_lock_denial_is_nowait_no_grant_or_retry(broad_db):
    p = proposal(broad_db)
    before, rows = state(broad_db), all_rows(broad_db)
    with owner(broad_db) as writer:
        writer.execute("BEGIN")
        try:
            writer.execute("UPDATE mf.broad_fixture SET value=10")
            document = spec.engine_document(p)
            with owner(broad_db) as conn:
                wrapped = Recorder(conn)
                with pytest.raises(policy.PolicyError, match="PRECOMMIT_ABORTED"):
                    policy.apply(
                        gate.guarded_connection(wrapped, p),
                        policy.Permit(document, policy.fingerprint(document)),
                    )
                assert not any(s in p["change"]["sql"] for s in wrapped.calls)
                assert wrapped.calls[-1] == "ROLLBACK"
        finally:
            writer.execute("ROLLBACK")
    assert state(broad_db) == before and all_rows(broad_db) == rows


def test_native_private_settings_in_new_domain_are_sealed_before_connection_boundary(broad_db):
    with psycopg.connect(broad_db, autocommit=True) as admin:
        admin.execute(
            "ALTER ROLE mfdata_verified_reader SET private.broad_password='SYNTHETIC_ROLE_MARKER'"
        )
    with owner(broad_db) as conn:
        conn.execute(
            "CREATE FUNCTION mf_analytics.private_broad() RETURNS int LANGUAGE SQL AS 'SELECT 1'"
        )
    with psycopg.connect(broad_db, autocommit=True) as admin:
        admin.execute(
            "ALTER FUNCTION mf_analytics.private_broad() SET private.broad_password='SYNTHETIC_FUNCTION_MARKER'"
        )
    before = state(broad_db)
    value = read_capture(broad_db)
    assert value["state"] == before == state(broad_db)
    raw = cs.canonical(value)
    assert "SYNTHETIC_ROLE_MARKER" not in raw and "SYNTHETIC_FUNCTION_MARKER" not in raw
    assert (
        next(r for r in value["state"]["roles"] if r["rolname"] == policy.READER)["rolconfig"][
            "is_null"
        ]
        is False
    )
    namespace = next(
        r["oid"] for r in value["state"]["namespaces"] if r["nspname"] == "mf_analytics"
    )
    function = next(
        r
        for r in value["state"]["functions"]
        if r["pronamespace"] == namespace and r["proname"] == "private_broad"
    )
    expected = b'["private.broad_password=SYNTHETIC_FUNCTION_MARKER"]'
    assert function["proconfig"] == dict(
        is_null=False, sha256=hashlib.sha256(expected).hexdigest(), utf8_bytes=len(expected)
    )
