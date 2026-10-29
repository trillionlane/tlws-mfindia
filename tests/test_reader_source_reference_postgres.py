"""Compile unchanged committed SQL in one disposable normal-CI database ONLY."""
from contextlib import contextmanager
from pathlib import Path
import subprocess
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
import pytest

import reader_source_reference as ref
from mfdataindia.store.postgres import PostgresStore

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[1]
ROLES = ("mfdata_admin", "mfdata_verified_generator", "mfdata_verified_reader", "mfdata_verified_admin")


@pytest.fixture
def source_reference_db(pg_dsn):
    if not pg_dsn:
        pytest.skip("MF_TEST_DSN not set")
    # Validate all source bytes BEFORE even opening the fixture admin connection.
    authority = ref.authority(ROOT)
    name = "mfdata_reference_" + uuid4().hex[:12]
    owner_dsn = make_conninfo(pg_dsn, dbname=name, user="mfdata_admin",
                             password="source-reference-ci-only")
    created = []
    database_created = False
    with psycopg.connect(pg_dsn, autocommit=True) as admin:
        assert not admin.execute("SELECT 1 FROM pg_roles WHERE rolname=ANY(%s)", (list(ROLES),)).fetchone()
        try:
            for role in ROLES:
                admin.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION NOBYPASSRLS PASSWORD 'source-reference-ci-only'").format(sql.Identifier(role)))
                created.append(role)
            admin.execute(sql.SQL("CREATE DATABASE {} OWNER mfdata_admin").format(sql.Identifier(name)))
            database_created = True
            core = [Path(r["path"]).name for r in authority["files"] if Path(r["path"]).parent == Path("sql")]
            optional = [Path(r["path"]).name for r in authority["files"] if Path(r["path"]).parent == Path("sql/optional")]
            assert len(core) == len(optional) == 22
            with PostgresStore(owner_dsn, use_copy=False) as store:
                assert store.apply_migrations(ROOT / "sql", files=tuple(core)) == core
                assert store.apply_migrations(ROOT / "sql/optional", files=tuple(optional)) == optional
            yield owner_dsn, authority
        finally:
            if database_created:
                # Every compiler connection has already closed before teardown.
                admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
            for role in reversed(created):
                admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


@contextmanager
def readonly(dsn):
    conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row, connect_timeout=5)
    try:
        with ref.reference_transaction(conn) as bounded:
            yield bounded
    finally:
        conn.close()  # Transport teardown only; never implicit rollback/retry.


def test_native_complete_committed_source_reference_and_numbered_lossless_output(source_reference_db, capsys):
    dsn, authority = source_reference_db
    with readonly(dsn) as conn:
        first = ref.collect(conn)
        second = ref.collect(conn)
        assert first == second
        # Metadata collection never invokes the target routine or reads business rows.
        for table in ("mf.funds", "mf.fund_facts", "mf.nav_history", "mf_review.events", "mf_fact_review.events"):
            assert conn.execute(sql.SQL("SELECT count(*) AS n FROM {}.{}").format(
                *[sql.Identifier(p) for p in table.split(".")])).fetchone()["n"] == 0
        ledger = conn.execute("SELECT migration_name,content_sha256 FROM mf.schema_migrations ORDER BY migration_name").fetchall()
        assert {r["migration_name"]: r["content_sha256"] for r in ledger} == {
            Path(r["path"]).name: r["sha256"] for r in authority["files"]}
        semantic = ref.semantic_reference(first)
        # Complete reference serialization is inside the SAME whole wall.
        ref.canonical(first)
        ref.canonical(semantic)
    assert semantic["runtime_authority"] is semantic["permission_acceptance"] is False
    assert semantic["raw_document_must_be_retained"] is True
    funcs = [o for o in first["objects"] if o["catalog"] == "pg_proc"]
    helper = [o for o in funcs if o["row"]["proname"] == "dependencies"
              and o["row"]["proargtypes"] == [23]]
    callee = [o for o in funcs if o["row"]["proname"] == "canonical_json"
              and o["row"]["proargtypes"] == [3802]]
    assert len(helper) == len(callee) == 1
    assert helper[0]["oid"] != 41849  # Real ephemeral OID, never normalized into DEV evidence.
    assert helper[0]["row"]["body_seal"]["sha256"] == "06f4c87653e1aa4423f2183e9f2476c114eec06224b6d166f1d188d7d8452aac"
    payload = {"format": "source-calibration-not-live-acceptance-v1", "authority": authority,
               "compiler_git": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
               "compiler_sha256": ref.digest((ROOT / "tests/reader_source_reference.py").read_bytes()),
               "raw": first, "semantic_reference": semantic,
               "permission_acceptance": False, "runtime_authority": False}
    lines = ref.reference_envelope(payload, identity=uuid4().hex)
    assert ref.extract_envelope(lines) == payload
    with capsys.disabled():
        for line in lines:
            print(line, flush=True)
