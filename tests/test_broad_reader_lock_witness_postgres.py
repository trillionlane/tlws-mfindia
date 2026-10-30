"""Disposable PostgreSQL-only calibration of the target/observer lock phases."""

from psycopg import sql
import pytest

from mfdataindia.review import broad_reader_lock_witness as witness
from test_reader_access_correction_postgres import owner


pytestmark = pytest.mark.postgres
pytest_plugins = ("test_reader_access_correction_postgres",)


def _snapshot(observer, pid):
    return [dict(row) for row in observer.execute(witness.LOCK_WITNESS_SQL, (pid,)).fetchall()]


def test_native_pristine_target_phase_growth_is_observed_by_other_backend(fixture_db):
    with owner(fixture_db) as setup:
        setup.execute("CREATE TABLE mf.lock_phase_fixture(value integer)")
        setup.execute("CREATE VIEW mf.lock_phase_view AS SELECT f.value FROM mf.lock_phase_fixture f "
                      "CROSS JOIN pg_catalog.pg_class c "
                      "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                      "LEFT JOIN pg_catalog.pg_inherits i ON i.inhrelid=c.oid "
                      "WHERE n.nspname='mf'")
        identities = [dict(row) for row in setup.execute(
            "SELECT c.oid::bigint AS oid,n.nspname,c.relname,c.relkind::text AS relkind "
            "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE (n.nspname='mf' AND c.relname IN ('lock_phase_fixture','lock_phase_view')) "
            "OR (n.nspname='pg_catalog' AND c.relname IN ('pg_class','pg_inherits','pg_namespace')) "
            "ORDER BY c.oid").fetchall()]
    view = next(row for row in identities if row["relkind"] == "v")
    leaves = [row for row in identities if row["nspname"] == "pg_catalog"]
    table = next(row for row in identities if row["relname"] == "lock_phase_fixture")
    classification = {"grant_roots": [table, view], "lock_only_leaves": leaves,
                      "materialized_unlocked": [],
                      "required_locks": [
                          {"oid": oid, "mode": mode} for oid, mode in sorted([
                              (table["oid"], "AccessExclusiveLock"),
                              (view["oid"], "AccessShareLock"),
                              *((r["oid"], "AccessShareLock") for r in leaves)])]}
    with owner(fixture_db) as target, owner(fixture_db) as observer:
        target.execute("BEGIN ISOLATION LEVEL READ COMMITTED READ ONLY")
        context = target.execute("SELECT pg_backend_pid() AS pid, "
                                 "(SELECT oid::bigint FROM pg_database WHERE datname=current_database()) "
                                 "AS database").fetchone()
        target.execute("ROLLBACK")
        observer_pid = observer.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
        assert observer_pid != context["pid"]
        baseline = _snapshot(observer, context["pid"])
        target.execute("BEGIN ISOLATION LEVEL READ COMMITTED READ WRITE")
        target.execute(sql.SQL("LOCK TABLE ONLY {}.{} IN ACCESS EXCLUSIVE MODE NOWAIT").format(
            sql.Identifier(table["nspname"]), sql.Identifier(table["relname"])))
        tables = _snapshot(observer, context["pid"])
        target.execute(sql.SQL("LOCK TABLE ONLY {}.{} IN ACCESS SHARE MODE NOWAIT").format(
            sql.Identifier(view["nspname"]), sql.Identifier(view["relname"])))
        views = _snapshot(observer, context["pid"])
        proof = witness.verify_phases(classification, baseline=baseline, tables=tables,
                                      views=views, target_pid=context["pid"],
                                      observer_pid=observer_pid, database_oid=context["database"])
        assert proof["grant_authorized"] is False
        assert {(row["oid"], row["mode"]) for row in proof["views"]} >= {
            (table["oid"], "AccessExclusiveLock"), (view["oid"], "AccessShareLock")}
        target.execute("ROLLBACK")
        assert _snapshot(observer, context["pid"]) == []
