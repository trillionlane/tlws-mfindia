"""Synthetic reproduction of restore deparser drift; no live/cloud write paths."""

from pathlib import Path
from types import SimpleNamespace
import psycopg
import pytest
from psycopg.rows import dict_row
from mfdataindia.enrichment import current_facts_store as current, monthly_projection, refresh_store
from mfdataindia.review.restored_facts import replay_owned_current_values
from test_official_fact_refresh import setup, append_period, receipt, references
from test_verified_review_store import connection, source_state
from test_verified_review_store import verified_db as verified_db

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.postgres
@pytest.mark.parametrize("version", [1, 2])
def test_replay_only_original_owned_view_and_preserve_exact_configuration(
    verified_db, monkeypatch, version
):
    selection, _, before = setup(verified_db, monkeypatch)
    if version == 2:
        snapshots = append_period(verified_db, monkeypatch, selection)
        with connection(verified_db) as conn:
            refresh_store.append_run(conn, receipt(snapshots, "restore-bootstrap"))
        with psycopg.connect(verified_db, row_factory=dict_row) as conn:
            monthly_projection.activate(
                conn, ROOT, legacy_snapshot_ids=[], reviewed=references(snapshots)
            )
    with connection(verified_db) as conn:
        original = current.capture(conn)
        definition = current.view_definition(conn, "mf_live_facts.current_values")
        # Reparse exactly the emitted SQL, as pg_dump/pg_restore does. PostgreSQL
        # adds AS text to unaliased UNION branches containing explicit casts.
        conn.execute("CREATE OR REPLACE VIEW mf_live_facts.current_values AS " + definition)
    with connection(verified_db) as conn:
        with pytest.raises(ValueError, match="view definitions differ"):
            current.capture(conn)
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        result = replay_owned_current_values(
            conn, ROOT, scratch_database=conn.info.dbname, backup_sha256="a" * 64
        )
        assert result["status"] == "ORIGINAL_REVIEWED_DDL_REPLAYED"
        assert current.capture(conn) == original
        assert source_state(conn) == before
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        assert (
            replay_owned_current_values(
                conn, ROOT, scratch_database=conn.info.dbname, backup_sha256="a" * 64
            )["status"]
            == "ALREADY_EXACT"
        )


@pytest.mark.parametrize(
    "host,port,name",
    [
        ("/cloudsql/trillionlane-dev:asia-south1:tlws-mf-data-dev", 55521, "mfdata_fs01_fixture"),
        ("127.0.0.1", 5432, "mfdata_fs01_fixture"),
        ("127.0.0.1", 5434, "mfdata_fs01_fixture"),
        ("127.0.0.1", 55521, "mfdataindia"),
        ("remote.example", 55521, "mfdata_fs01_fixture"),
    ],
)
def test_source_prod_and_unbounded_targets_rejected_before_any_sql(host, port, name):
    conn = SimpleNamespace(info=SimpleNamespace(host=host, port=port, dbname=name))
    with pytest.raises(ValueError, match="never source/prod"):
        replay_owned_current_values(conn, ROOT, scratch_database=name, backup_sha256="a" * 64)


@pytest.mark.postgres
def test_semantic_drift_is_not_normalized_or_resealed(verified_db, monkeypatch):
    setup(verified_db, monkeypatch)
    with connection(verified_db) as conn:
        cfg = current.configuration(conn)
        conn.execute(
            "CREATE OR REPLACE VIEW mf_live_facts.current_values AS SELECT amfi_scheme_code AS code,'{}'::jsonb AS values FROM mf.fund_facts WHERE false"
        )
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        with pytest.raises(ValueError, match="internal-alias-only"):
            replay_owned_current_values(
                conn, ROOT, scratch_database=conn.info.dbname, backup_sha256="a" * 64
            )
    with connection(verified_db, readonly=True) as conn:
        assert current.configuration(conn) == cfg
