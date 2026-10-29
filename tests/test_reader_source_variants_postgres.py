"""Empty, fresh normal-CI source databases ONLY; no live/provider admission."""

from pathlib import Path
import subprocess
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
import pytest

import reader_source_reference as legacy
import reader_source_variants as variants
from mfdataindia.store.postgres import PostgresStore

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[1]
EMPTY = (
    "mf.funds",
    "mf.fund_facts",
    "mf.nav_history",
    "mf_review.policies",
    "mf_live_facts.configuration",
    "mf_book_facts.configuration",
    "mf_book_facts.batches",
    "mf_book_facts.runs",
)


@pytest.fixture
def source_variant_db(pg_dsn):
    if not pg_dsn:
        pytest.skip("MF_TEST_DSN not set")
    # ALL eight variant pins and every original44 source precede admin connect.
    authority = variants.variant_authority(ROOT)
    name = "mfdata_reference_" + uuid4().hex[:12]
    dsn = make_conninfo(
        pg_dsn, dbname=name, user="mfdata_admin", password="source-reference-ci-only"
    )
    created = []
    allocated = False
    with psycopg.connect(pg_dsn, autocommit=True) as admin:
        assert not admin.execute(
            "SELECT 1 FROM pg_roles WHERE rolname=ANY(%s)", (list(legacy.GOVERNED_ROLES),)
        ).fetchone()
        try:
            for role in legacy.GOVERNED_ROLES:
                admin.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION NOBYPASSRLS PASSWORD 'source-reference-ci-only'"
                    ).format(sql.Identifier(role))
                )
                created.append(role)
            admin.execute(
                sql.SQL("CREATE DATABASE {} OWNER mfdata_admin").format(sql.Identifier(name))
            )
            allocated = True
            core = [
                Path(r["path"]).name
                for r in authority["base"]["files"]
                if Path(r["path"]).parent == Path("sql")
            ]
            optional = [
                Path(r["path"]).name
                for r in authority["base"]["files"]
                if Path(r["path"]).parent == Path("sql/optional")
            ]
            assert len(core) == len(optional) == 22
            with PostgresStore(dsn, use_copy=False) as store:
                assert store.apply_migrations(ROOT / "sql", files=tuple(core)) == core
                assert (
                    store.apply_migrations(ROOT / "sql/optional", files=tuple(optional)) == optional
                )
            yield dsn, authority
        finally:
            # Normal disposable-CI fixture cleanup, never live automatic reversal.
            if allocated:
                admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
            for role in reversed(created):
                admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def observe(dsn, authority, profile):
    compiler_git = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    source_pins = {
        path: legacy.digest((ROOT / path).read_bytes())
        for path in ("tests/reader_source_reference.py", "tests/reader_source_variants.py")
    }
    conn = psycopg.connect(dsn, autocommit=True, connect_timeout=5, row_factory=dict_row)
    try:
        with legacy.reference_transaction(conn) as bounded:
            first = variants.collect_extended(bounded)
            assert first == variants.collect_extended(bounded)
            grant_state = variants.query(
                bounded, variants.inventory.engine.STATE_SQL, (), ("value",)
            )
            assert len(grant_state) == 1
            grant_state = grant_state[0]["value"]
            variants.state_shape(grant_state)
            for table in EMPTY:
                names = [sql.Identifier(p) for p in table.split(".")]
                with bounded.execute(
                    sql.SQL("SELECT count(*) AS n FROM {}.{}").format(*names), prepare=False
                ) as cursor:
                    rows = cursor.fetchall()
                    assert cursor.statusmessage == "SELECT 1" and cursor.rowcount == len(rows) == 1
                    assert rows[0]["n"] == 0
            ledger = variants.query(
                bounded,
                "SELECT migration_name,content_sha256 FROM mf.schema_migrations ORDER BY migration_name",
                (),
                ("migration_name", "content_sha256"),
            )
            assert {r["migration_name"]: r["content_sha256"] for r in ledger} == {
                Path(r["path"]).name: r["sha256"] for r in authority["base"]["files"]
            }
            portable = variants.portable_source_reference(first)
            payload = dict(
                format="extended-source-calibration-NOT-live-acceptance-v1",
                profile=profile,
                compiler_git=compiler_git,
                compiler_sources=source_pins,
                base_authority_sha256=legacy.AUTHORITY_SHA,
                variant_authority_sha256=variants.AUTHORITY_SHA,
                authority=authority,
                raw=first,
                grant_state=grant_state,
                portable_sha256=legacy.digest(legacy.canonical(portable).encode()),
                portable_projection_format=portable["format"],
                raw_sha256=legacy.digest(legacy.canonical(first).encode()),
                runtime_authority=False,
                permission_acceptance=False,
            )
            assert len(legacy.canonical(payload).encode()) <= legacy.MAX_RAW
            bounded.remaining()
        assert conn.info.transaction_status == legacy.TransactionStatus.IDLE
    finally:
        conn.close()  # No context-manager rollback after a spent/ambiguous wall.
    return payload


def views(raw):
    namespaces = {
        o["oid"]: o["row"]["nspname"] for o in raw["objects"] if o["catalog"] == "pg_namespace"
    }
    return {
        (namespaces[o["row"]["relnamespace"]], o["row"]["relname"]): o["row"][
            "view_definition_seal"
        ]
        for o in raw["objects"]
        if o["catalog"] == "pg_class" and o["row"]["relkind"] in ("v", "m")
    }


def facade_columns(raw):
    namespaces = {
        o["oid"]: o["row"]["nspname"] for o in raw["objects"] if o["catalog"] == "pg_namespace"
    }
    objects = [
        o
        for o in raw["objects"]
        if o["catalog"] == "pg_class"
        and namespaces[o["row"]["relnamespace"]] == "mf"
        and o["row"]["relname"] == "current_fund_facts"
    ]
    assert len(objects) == 1
    return [r for r in raw["columns"] if r["attrelid"] == objects[0]["oid"]]


def test_native_exact_book_v2_single_pass_extended_references(source_variant_db, capsys):
    dsn, authority = source_variant_db
    expected_git = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    base = observe(dsn, authority, variants.PROFILES[0])
    conn = psycopg.connect(dsn, autocommit=True, connect_timeout=5, row_factory=dict_row)
    try:
        variants.install_book_variant(conn, ROOT)
        assert conn.info.transaction_status == legacy.TransactionStatus.IDLE
    finally:
        conn.close()
    book = observe(dsn, authority, variants.PROFILES[1])
    variants.assert_source_only_transition(base, book)
    before, after = base["raw"]["legacy"], book["raw"]["legacy"]
    old_views, new_views = views(before), views(after)
    assert set(old_views) == set(new_views)
    assert {key for key in old_views if old_views[key] != new_views[key]} == {
        ("mf_live_facts", "field_versions"),
        ("mf_live_facts", "current_values"),
        ("mf_live_facts", "resolved"),
        ("mf", "current_fund_facts"),
    }
    assert new_views[("mf", "current_fund_facts")] == new_views[("mf_live_facts", "resolved")]
    assert facade_columns(before) == facade_columns(
        after
    )  # All real types/typmods/collations/order preserved.
    for payload in (base, book):
        outside = [
            r for r in payload["raw"]["relation_candidate"] if r["nspname"] not in legacy.SCHEMAS
        ]
        assert {(r["nspname"], r["relname"]) for r in outside} == {
            ("pg_catalog", "pg_class"),
            ("pg_catalog", "pg_inherits"),
            ("pg_catalog", "pg_namespace"),
        }
        lines = variants.named_envelope(payload, identity=uuid4().hex)
        assert (
            variants.extract_named(lines, payload["profile"], expected_git=expected_git) == payload
        )
        with capsys.disabled():
            for line in lines:
                print(line, flush=True)
