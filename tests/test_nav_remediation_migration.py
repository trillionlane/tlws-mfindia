"""Migration 021 under the actual restore defaults and mfdata_admin ownership."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from mfdataindia.store.postgres import DEFAULT_MIGRATIONS, PostgresStore

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROLES = ("mfdata_app", "mfdata_ingest", "mfdata_association_writer", "mfdata_audit")
REVIEW_TABLES = (
    "nav_remediation_snapshots",
    "nav_remediation_decisions",
    "nav_remediation_proposals",
)


@pytest.mark.postgres
def test_021_removes_production_default_grants_from_review_ledger(pg_dsn, monkeypatch):
    if not pg_dsn:
        pytest.skip("MF_TEST_DSN not set")
    roles = ("mfdata_admin", *RUNTIME_ROLES, "mfdata_remediation_review")
    name = "mfdata_review_grants_" + uuid4().hex[:12]
    dsn = make_conninfo(pg_dsn, dbname=name)
    with psycopg.connect(pg_dsn, autocommit=True) as admin:
        existing = admin.execute(
            "SELECT rolname FROM pg_roles WHERE rolname=ANY(%s)", (list(roles),)
        ).fetchall()
        if existing:
            pytest.skip(
                "production-named test roles preexist; refusing to alter shared cluster roles"
            )
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        created = []
        try:
            for role in roles:
                # Administrative role is privileged only for this disposable
                # migration test; runtime roles are ordinary NOLOGIN principals.
                attributes = (
                    sql.SQL("NOLOGIN SUPERUSER") if role == "mfdata_admin" else sql.SQL("NOLOGIN")
                )
                admin.execute(sql.SQL("CREATE ROLE {} {}").format(sql.Identifier(role), attributes))
                created.append(role)
            with PostgresStore(dsn, use_copy=False) as store:
                store.apply_migrations(
                    ROOT / "sql", files=tuple(f for f in DEFAULT_MIGRATIONS if f < "021_")
                )

            spec = importlib.util.spec_from_file_location(
                "restore_defaults_under_test", ROOT / "scripts/restore_snapshot.py"
            )
            restore = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(restore)
            statements = []
            monkeypatch.setattr(
                restore, "run", lambda *args, **kwargs: statements.append(kwargs["input_text"])
            )
            restore.apply_runtime_grants(env={})
            # Only the disposable database identifier differs from production.
            grants = statements[0].replace("ON DATABASE mfdataindia", f'ON DATABASE "{name}"')
            with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
                conn.execute(grants)
                conn.execute("SET ROLE mfdata_admin")
                conn.execute("CREATE TABLE mf.restore_default_control (value integer)")
                control = conn.execute(
                    "SELECT has_table_privilege('mfdata_ingest','mf.restore_default_control','INSERT') AS ingest_insert, "
                    "has_table_privilege('mfdata_app','mf.restore_default_control','SELECT') AS app_select"
                ).fetchone()
                assert control == {"ingest_insert": True, "app_select": True}

                conn.execute((ROOT / "sql/021_nav_remediation_review.sql").read_text())
                assert (
                    conn.execute(
                        "SELECT pg_get_userbyid(relowner) AS owner FROM pg_class WHERE oid='mf.nav_remediation_snapshots'::regclass"
                    ).fetchone()["owner"]
                    == "mfdata_admin"
                )
                for table in REVIEW_TABLES:
                    for role in RUNTIME_ROLES:
                        assert (
                            conn.execute(
                                "SELECT has_table_privilege(%s,%s,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS allowed",
                                (role, f"mf.{table}"),
                            ).fetchone()["allowed"]
                            is False
                        )
                    assert conn.execute(
                        "SELECT has_table_privilege('mfdata_remediation_review',%s,'INSERT') AS insert, "
                        "has_table_privilege('mfdata_remediation_review',%s,'UPDATE,DELETE,TRUNCATE') AS rewrite",
                        (f"mf.{table}", f"mf.{table}"),
                    ).fetchone() == {"insert": True, "rewrite": False}
                assert (
                    conn.execute(
                        "SELECT has_function_privilege('mfdata_ingest','mf.reject_remediation_rewrite()','EXECUTE') AS allowed"
                    ).fetchone()["allowed"]
                    is False
                )
                # The isolation is table-specific: normal source defaults remain.
                conn.execute("CREATE TABLE mf.restore_future_control (value integer)")
                assert (
                    conn.execute(
                        "SELECT has_table_privilege('mfdata_ingest','mf.restore_future_control','INSERT') AS allowed"
                    ).fetchone()["allowed"]
                    is True
                )

            for role in RUNTIME_ROLES:
                for table in REVIEW_TABLES:
                    with psycopg.connect(dsn) as conn:
                        conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
                        with pytest.raises(psycopg.errors.InsufficientPrivilege):
                            conn.execute(
                                sql.SQL("INSERT INTO mf.{} DEFAULT VALUES").format(
                                    sql.Identifier(table)
                                )
                            )
        finally:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s", (name,)
            )
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
            for role in reversed(created):
                admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
