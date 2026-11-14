"""Real disposable hosted PostgreSQL only; reuse existing migrated RO01 fixture."""

from datetime import date
import importlib.util
from pathlib import Path
import sys

from fastapi.testclient import TestClient
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
import pytest

from mfdataindia.datasets import policy as datasets
from mfdataindia.datasets.app import create_app
from mfdataindia.store.postgres import PostgresStore
from mfdataindia.tagging import policy as p, source_evidence, store
from test_ro01_reader_policy_postgres import (
    ro01_db as ro01_db,
    connect,
    adopt,
    PASSWORD,
    generator_capabilities,
)

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[1]


def append_source_receipt(conn, *, source_kind="full-catalogue"):
    snapshot = conn.execute("""SELECT snapshot_run_id,snapshot_date
        FROM mf.source_snapshot_runs WHERE snapshot_scope='FULL'
        ORDER BY snapshot_run_id DESC LIMIT 1""").fetchone()
    return source_evidence.append(
        conn,
        source_kind=source_kind,
        source_snapshot_run_id=snapshot["snapshot_run_id"],
        catalogue_observed_on=snapshot["snapshot_date"],
        observed_on=date(2026, 11, 11),
    )


@pytest.fixture
def tagging_db(ro01_db, pg_dsn):
    # The existing fixture enforces GITHUB_ACTIONS=true and creates a disposable DB.
    with psycopg.connect(pg_dsn, autocommit=True) as admin:
        admin.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
            ).format(sql.Identifier(p.WRITER), sql.Literal(PASSWORD))
        )
        try:
            adopt(ro01_db)
            with PostgresStore(ro01_db, use_copy=False) as database:
                database.apply_migrations(
                    ROOT / "sql" / "optional",
                    files=("046_fund_family_continuity.sql", "047_tag_input_publications.sql"),
                )
            with connect(ro01_db, autocommit=False) as owner:
                owner.execute(
                    "UPDATE mf.funds SET last_seen_in_source='2026-10-31',plan_source='COLUMN',scheme_category='Equity Scheme - Large Cap Fund'"
                )
                append_source_receipt(owner)
                assert store.install(owner, ROOT)["state"] == "INSTALLED"
                assert store.install(owner, ROOT)["state"] == "ALREADY_INSTALLED"
            yield ro01_db
        finally:
            # Release only this synthetic role's fixed fixture-owned grants.
            with psycopg.connect(make_conninfo(pg_dsn, dbname="mfdataindia")) as owner:
                owner.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(p.WRITER)))
            admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(p.WRITER)))


def writer(dsn):
    return psycopg.connect(
        make_conninfo(dsn, user=p.WRITER, password=PASSWORD), autocommit=True, row_factory=dict_row
    )


def family_script():
    path = ROOT / "scripts" / "build_fund_family.py"
    spec = importlib.util.spec_from_file_location("atomic_build_fund_family", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("ro01_db", ["core"], indirect=True)
def test_native_atomic_publication_dataset_reads_reentry_and_rebuild_preservation(
    tagging_db, monkeypatch
):
    dsn = tagging_db
    before_generator = generator_capabilities(dsn)
    with writer(dsn) as conn:
        first = store.publish(conn)
        assert first["state"] == "PUBLISHED" and first["funds"] == 2
        assert first["mapping_counts"] == {"FAMILY_MISSING": 2}
        old_rows = conn.execute(
            "SELECT * FROM mf_tagging.assessments ORDER BY code,dimension"
        ).fetchall()
        assert store.publish(conn)["state"] == "UNCHANGED"
        assert conn.execute("SELECT count(*) AS n FROM mf_tagging.snapshots").fetchone()["n"] == 1
    reader_dsn = make_conninfo(dsn, user=p.READER, password=PASSWORD)
    with TestClient(create_app(reader_dsn)) as client:
        dataset = datasets.dataset_id("mf_tagging", "current")
        discovered = client.get("/api/datasets/v1")
        assert discovered.status_code == 200
        entry = next(d for d in discovered.json()["datasets"] if d["id"] == dataset)
        assert entry["state"] == "available"
        one = client.get("/api/datasets/v1/" + dataset + "/rows", params={"limit": 1})
        assert one.status_code == 200
        body = one.json()
        assert body["rows"][0]["document"]["snapshot_id"] == first["snapshot_id"]
        assert body["rows"][0]["document"]["state"] == "supported"
        two = client.get(
            "/api/datasets/v1/" + dataset + "/rows",
            params={"limit": 1, "cursor": body["next_cursor"]},
        )
        assert two.status_code == 200 and two.json()["rows"] != body["rows"]
        assert client.post("/api/datasets/v1/" + dataset + "/rows", json={}).status_code == 405
        assert (
            client.get(
                "/api/datasets/v1/" + datasets.dataset_id("mf_tagging", "snapshots")
            ).status_code
            == 404
        )
    # A family-only source rebuild leaves prior assessments immutable until the
    # dedicated writer publishes the newly attested projection.
    module = family_script()
    monkeypatch.setattr(sys, "argv", ["build_fund_family.py", "--dsn", dsn])
    monkeypatch.setenv("MF_TEST_NO_COPY", "1")
    original_append = module.source_evidence.append
    with connect(dsn) as owner:
        before_family = owner.execute("SELECT * FROM mf.fund_family ORDER BY group_key").fetchall()
        before_receipt = module.source_evidence.latest(owner)

    def fail_after_rebuild(*args, **kwargs):
        raise p.TaggingError("SYNTHETIC_RECEIPT_FAILURE")

    monkeypatch.setattr(module.source_evidence, "append", fail_after_rebuild)
    with pytest.raises(p.TaggingError, match="SYNTHETIC_RECEIPT_FAILURE"):
        module.main()
    with connect(dsn) as owner:
        assert owner.execute("SELECT * FROM mf.fund_family ORDER BY group_key").fetchall() == before_family
        assert module.source_evidence.latest(owner) == before_receipt

    monkeypatch.setattr(module.source_evidence, "append", original_append)
    assert module.main() == 0
    with connect(dsn) as owner:
        rebuilt_receipt = module.source_evidence.latest(owner)
        assert rebuilt_receipt["publication_sequence"] > before_receipt["publication_sequence"]
    with writer(dsn) as conn:
        assert (
            conn.execute("SELECT * FROM mf_tagging.assessments ORDER BY code,dimension").fetchall()
            == old_rows
        )
        changed = store.publish(conn)
        assert changed["state"] == "PUBLISHED" and changed["snapshot_id"] != first["snapshot_id"]
        assert changed["mapping_counts"] == {"FAMILY_VERIFIED": 2}
    assert generator_capabilities(dsn) == before_generator


@pytest.mark.parametrize("ro01_db", ["core"], indirect=True)
def test_native_reader_and_writer_cannot_mutate_source_or_elevate(tagging_db):
    with connect(tagging_db, p.READER) as conn:
        for statement in (
            "INSERT INTO mf_tagging.current VALUES(9,'theme','{}')",
            "UPDATE mf_tagging.current SET document='{}'",
            "DELETE FROM mf_tagging.current",
            "CREATE TABLE mf_tagging.denied(id int)",
            "SELECT * FROM mf_tagging.snapshots",
            "ALTER ROLE mfdata_verified_reader CREATEROLE",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)
        with pytest.raises(p.TaggingError, match="TAGGING_DIRECT_PRINCIPAL_REQUIRED"):
            store.publish(conn)
    with writer(tagging_db) as conn:
        for statement in (
            "UPDATE mf.funds SET scheme_name='wrong'",
            "DELETE FROM mf.funds",
            "CREATE TABLE mf_tagging.denied(id int)",
            "DELETE FROM mf_tagging.assessments",
            "ALTER ROLE mfdata_tag_writer CREATEROLE",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)
        assert store.publish(conn)["state"] == "PUBLISHED"


@pytest.mark.parametrize("ro01_db", ["core"], indirect=True)
def test_native_failure_after_projection_delete_rolls_back_head_and_history(
    tagging_db, monkeypatch
):
    with writer(tagging_db) as conn:
        old = store.publish(conn)["snapshot_id"]
    with connect(tagging_db, autocommit=False) as owner:
        owner.execute("UPDATE mf.funds SET last_seen_in_source='2026-11-01'")
        append_source_receipt(owner)
    original = store._insert

    def fail_current(conn, table, rows):
        if table == "current":
            raise p.TaggingError("SYNTHETIC_PUBLICATION_FAILURE")
        return original(conn, table, rows)

    monkeypatch.setattr(store, "_insert", fail_current)
    with writer(tagging_db) as conn:
        with pytest.raises(p.TaggingError, match="SYNTHETIC_PUBLICATION_FAILURE"):
            store.publish(conn)
        assert (
            conn.execute("SELECT snapshot_id FROM mf_tagging.head").fetchone()["snapshot_id"] == old
        )
        assert conn.execute("SELECT count(*) AS n FROM mf_tagging.current").fetchone()[
            "n"
        ] == 2 * len(p.DIMENSIONS)
        assert conn.execute("SELECT count(*) AS n FROM mf_tagging.snapshots").fetchone()["n"] == 1
    monkeypatch.setattr(store, "_insert", original)
    with writer(tagging_db) as conn:
        assert store.publish(conn)["state"] == "PUBLISHED"
        assert conn.execute("SELECT count(*) AS n FROM mf_tagging.snapshots").fetchone()["n"] == 2


@pytest.mark.parametrize("ro01_db", ["core"], indirect=True)
def test_native_missing_grant_unsafe_grant_and_concurrent_publisher_fail_closed(tagging_db):
    with connect(tagging_db, autocommit=False) as owner:
        owner.execute("GRANT UPDATE ON mf_tagging.current TO mfdata_verified_reader")
    with TestClient(
        create_app(make_conninfo(tagging_db, user=p.READER, password=PASSWORD))
    ) as client:
        response = client.get(
            "/api/datasets/v1/" + datasets.dataset_id("mf_tagging", "current") + "/rows"
        )
        assert (
            response.status_code == 503 and response.json()["detail"] == "UNSAFE_DATASET_CAPABILITY"
        )
    with connect(tagging_db, autocommit=False) as owner:
        owner.execute("REVOKE UPDATE,SELECT ON mf_tagging.current FROM mfdata_verified_reader")
    with TestClient(
        create_app(make_conninfo(tagging_db, user=p.READER, password=PASSWORD))
    ) as client:
        response = client.get(
            "/api/datasets/v1/" + datasets.dataset_id("mf_tagging", "current") + "/rows"
        )
        assert (
            response.status_code == 503
            and response.json()["detail"] == "DATASET_READ_GRANTS_MISSING"
        )
    with connect(tagging_db, autocommit=False) as owner, writer(tagging_db) as conn:
        store.limits(owner)
        with pytest.raises(p.TaggingError, match="TAGGING_PUBLICATION_BUSY"):
            store.publish(conn)
        assert conn.execute("SELECT count(*) AS n FROM mf_tagging.snapshots").fetchone()["n"] == 0


@pytest.mark.parametrize("ro01_db", ["core"], indirect=True)
def test_native_unexpected_source_column_write_rejected_before_snapshot(tagging_db):
    with connect(tagging_db, autocommit=False) as owner:
        owner.execute("GRANT UPDATE(scheme_name) ON mf.funds TO mfdata_tag_writer")
    with writer(tagging_db) as conn:
        with pytest.raises(p.TaggingError, match="TAGGING_UNSAFE_WRITER"):
            store.publish(conn)
        assert conn.execute("SELECT count(*) AS n FROM mf_tagging.snapshots").fetchone()["n"] == 0


@pytest.mark.parametrize("ro01_db", ["core"], indirect=True)
def test_native_same_head_damaged_projection_or_history_never_reports_unchanged(tagging_db):
    with writer(tagging_db) as conn:
        initial = store.publish(conn)
    with connect(tagging_db, autocommit=False) as owner:
        owner.execute("DELETE FROM mf_tagging.current WHERE code=1")
    with writer(tagging_db) as conn:
        with pytest.raises(p.TaggingError, match="TAGGING_PUBLICATION_INCOMPLETE"):
            store.publish(conn)
        assert conn.execute("SELECT count(*) AS n FROM mf_tagging.snapshots").fetchone()["n"] == 1
        assert (
            conn.execute("SELECT snapshot_id FROM mf_tagging.head").fetchone()["snapshot_id"]
            == initial["snapshot_id"]
        )
    # Fixture-only owner reconciliation restores known original projection; publisher never repairs it.
    with connect(tagging_db, autocommit=False) as owner:
        owner.execute(
            "INSERT INTO mf_tagging.current SELECT code,dimension,document FROM mf_tagging.assessments WHERE snapshot_id=%s AND code=1",
            (initial["snapshot_id"],),
        )
        owner.execute("UPDATE mf_tagging.observations SET document='{}' WHERE code=1")
    with writer(tagging_db) as conn:
        with pytest.raises(p.TaggingError, match="TAGGING_HISTORY_DIFFERS"):
            store.publish(conn)
        assert conn.execute("SELECT count(*) AS n FROM mf_tagging.snapshots").fetchone()["n"] == 1


@pytest.mark.parametrize("ro01_db", ["core"], indirect=True)
def test_native_definitions_from_other_version_survive_current_publication(tagging_db):
    with writer(tagging_db) as conn:
        candidate = store.census(conn)
        first = candidate["definitions"][0]
        conn.execute(
            "INSERT INTO mf_tagging.definitions VALUES(%s,%s,%s,%s)",
            ("0" * 64, "synthetic-previous-version", first["dimension"], first["value"]),
        )
        assert store.publish(conn)["state"] == "PUBLISHED"
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM mf_tagging.definitions WHERE dimension=%s AND value=%s",
                (first["dimension"], first["value"]),
            ).fetchone()["n"]
            == 2
        )
        assert store.publish(conn)["state"] == "UNCHANGED"


@pytest.mark.parametrize("ro01_db", ["core"], indirect=True)
def test_native_same_day_family_a_b_a_creates_ordered_transition_receipts(
    tagging_db, monkeypatch
):
    dsn = tagging_db
    module = family_script()
    monkeypatch.setattr(sys, "argv", ["build_fund_family.py", "--dsn", dsn])
    monkeypatch.setenv("MF_TEST_NO_COPY", "1")
    assert module.main() == 0

    with connect(dsn) as owner:
        first_a = source_evidence.latest(owner)
    with connect(dsn, autocommit=False) as owner:
        owner.execute("UPDATE mf.fund_family SET group_key='1B' WHERE group_key='1'")
        owner.execute("UPDATE mf.fund_variants SET group_key='1B' WHERE group_key='1'")
        b = append_source_receipt(owner, source_kind="fund-family-rebuild")
    with connect(dsn, autocommit=False) as owner:
        owner.execute("UPDATE mf.fund_family SET group_key='1' WHERE group_key='1B'")
        owner.execute("UPDATE mf.fund_variants SET group_key='1' WHERE group_key='1B'")
        second_a = append_source_receipt(owner, source_kind="fund-family-rebuild")

    assert first_a["family_projection_hash"] != b["family_projection_hash"]
    assert first_a["family_projection_hash"] == second_a["family_projection_hash"]
    assert first_a["publication_id"] != second_a["publication_id"]
    assert first_a["publication_sequence"] < b["publication_sequence"] < second_a["publication_sequence"]
    assert b["predecessor_publication_id"] == first_a["publication_id"]
    assert second_a["predecessor_publication_id"] == b["publication_id"]
    with writer(dsn) as conn:
        assert store.publish(conn)["state"] == "PUBLISHED"
