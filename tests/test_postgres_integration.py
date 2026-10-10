"""Integration tests against a live PostgreSQL server.

Skipped unless ``MF_TEST_DSN`` is set, so the unit suite stays fast and
dependency-free. To run them:

    docker compose up -d
    export MF_TEST_DSN="host=127.0.0.1 port=5432 dbname=mfdataindia user=mfdata password=mfdata"
    pytest -m postgres

Set ``MF_TEST_NO_COPY=1`` for servers whose COPY sub-protocol is incomplete
(e.g. the PGlite wire-protocol bridge used for local validation).

WARNING: these tests DELETE from mf.* tables in the target database. Point
MF_TEST_DSN at a scratch database only.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mfdataindia.api import queries
from mfdataindia.api.app import create_app
from mfdataindia.api.association_app import create_association_app
from mfdataindia.api.queries import FAMILY_ORDER
from mfdataindia.load.amfi_to_store import fund_rows, load_parsed_amfi
from mfdataindia.store.postgres import DEFAULT_MIGRATIONS, PostgresStore

pytestmark = pytest.mark.postgres

_TABLES_TO_CLEAR = (
    "association_tag_idempotency",
    "fund_family_association_tags",
    "fund_family_association_state",
    "fund_family",
    "source_snapshot_runs",
    "source_metadata",
    "ingest_checkpoints",
    "quality_flags",
    "fund_variants",
    "nav_history",
    "funds",
    "amcs",
)


def test_api_health_uses_the_postgres_pool(pg_dsn):
    if not pg_dsn:
        pytest.skip("MF_TEST_DSN not set")
    with TestClient(create_app(pg_dsn)) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "PostgreSQL 18" in response.json()["db"]


def test_private_association_writer_is_idempotent_and_versioned(pg_dsn, repo_root):
    if not pg_dsn:
        pytest.skip("MF_TEST_DSN not set")
    with PostgresStore(pg_dsn) as setup:
        setup.apply_migrations(Path(repo_root) / "sql")
        with setup.transaction() as conn:
            conn.execute(
                """
                INSERT INTO mf.fund_family
                    (tlws_mf_id, group_key, base_scheme_name, slug, tags)
                VALUES ('11111111-1111-1111-1111-111111111111', 'API-11-GROUP', 'API 11 Fund',
                        'api-11-fund', ARRAY['equity'])
                ON CONFLICT (tlws_mf_id) DO NOTHING
                """
            )

    payload = {
        "expected_version": 0,
        "tags": [
            {
                "value": "Nippon India Taiwan Equity Fund",
                "type": "scheme_alias",
                "source": "trillion-insights",
            }
        ],
    }
    try:
        with TestClient(create_association_app(pg_dsn, writes_enabled=True)) as client:
            first = client.post(
                "/api/fund-families/11111111-1111-1111-1111-111111111111/association-tags",
                headers={"Idempotency-Key": "api-11-stable-001"},
                json=payload,
            )
            assert first.status_code == 201
            assert first.json() == {
                "tlws_mf_id": "11111111-1111-1111-1111-111111111111",
                "previous_version": 0,
                "version": 1,
                "added": [
                    {
                        "value": "nippon-india-taiwan-equity-fund",
                        "type": "scheme_alias",
                        "source": "trillion-insights",
                    }
                ],
                "tags": [
                    {
                        "value": "nippon-india-taiwan-equity-fund",
                        "type": "scheme_alias",
                        "source": "trillion-insights",
                    }
                ],
            }

            replay = client.post(
                "/api/fund-families/11111111-1111-1111-1111-111111111111/association-tags",
                headers={"Idempotency-Key": "api-11-stable-001"},
                json=payload,
            )
            assert replay.status_code == 201
            assert replay.json() == first.json()

            current = client.get(
                "/api/fund-families/11111111-1111-1111-1111-111111111111/association-tags"
            )
            assert current.status_code == 200
            assert current.json()["version"] == 1

            no_op_payload = dict(payload, expected_version=1)
            no_op = client.post(
                "/api/fund-families/11111111-1111-1111-1111-111111111111/association-tags",
                headers={"Idempotency-Key": "api-11-stable-002"},
                json=no_op_payload,
            )
            assert no_op.status_code == 200
            assert no_op.json()["version"] == 1
            assert no_op.json()["added"] == []

            stale = client.post(
                "/api/fund-families/11111111-1111-1111-1111-111111111111/association-tags",
                headers={"Idempotency-Key": "api-11-stable-003"},
                json=payload,
            )
            assert stale.status_code == 409
            assert stale.json()["current_version"] == 1

            reused = client.post(
                "/api/fund-families/11111111-1111-1111-1111-111111111111/association-tags",
                headers={"Idempotency-Key": "api-11-stable-001"},
                json={
                    "expected_version": 1,
                    "tags": [
                        {
                            "value": "different-alias",
                            "type": "scheme_alias",
                            "source": "trillion-insights",
                        }
                    ],
                },
            )
            assert reused.status_code == 409
    finally:
        with PostgresStore(pg_dsn) as cleanup, cleanup.transaction() as conn:
            conn.execute(
                "DELETE FROM mf.association_tag_idempotency WHERE idempotency_key LIKE 'api-11-stable-%'"
            )
            conn.execute(
                "DELETE FROM mf.fund_family_association_tags WHERE tlws_mf_id = '11111111-1111-1111-1111-111111111111'"
            )
            conn.execute(
                "DELETE FROM mf.fund_family_association_state WHERE tlws_mf_id = '11111111-1111-1111-1111-111111111111'"
            )
            conn.execute(
                "DELETE FROM mf.fund_family WHERE tlws_mf_id = '11111111-1111-1111-1111-111111111111'"
            )


def test_association_writer_migration_grants_only_required_privileges(pg_dsn, repo_root):
    if not pg_dsn:
        pytest.skip("MF_TEST_DSN not set")
    psycopg = pytest.importorskip("psycopg")
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    admin = psycopg.connect(pg_dsn, autocommit=True)
    role = "mfdata_association_writer"
    cloudsql_role = "cloudsqlsuperuser"
    dbname = f"mfdata_association_privileges_{os.getpid()}"
    preexisting = admin.execute(
        "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)
    ).fetchone()
    if preexisting:
        admin.close()
        pytest.skip(f"role {role} already exists; refusing to alter shared cluster state")
    cloudsql_role_preexisting = admin.execute(
        "SELECT 1 FROM pg_roles WHERE rolname = %s", (cloudsql_role,)
    ).fetchone()
    if not cloudsql_role_preexisting:
        admin.execute(f'CREATE ROLE "{cloudsql_role}" NOLOGIN')
    admin.execute(f'CREATE ROLE "{role}" NOLOGIN CREATEDB CREATEROLE')
    admin.execute(f'GRANT "{cloudsql_role}" TO "{role}"')
    admin.execute(f'CREATE DATABASE "{dbname}"')
    params = conninfo_to_dict(pg_dsn)
    params["dbname"] = dbname
    scratch_dsn = make_conninfo(**params)
    try:
        with PostgresStore(scratch_dsn) as scratch:
            scratch.apply_migrations(Path(repo_root) / "sql")
            conn = scratch.connect()
            checks = conn.execute(
                """
                SELECT
                    has_table_privilege(%s, 'mf.fund_family', 'SELECT') AS family_select,
                    has_table_privilege(%s, 'mf.fund_family', 'UPDATE') AS family_update,
                    has_table_privilege(%s, 'mf.fund_family_association_state', 'SELECT') AS state_select,
                    has_table_privilege(%s, 'mf.fund_family_association_state', 'INSERT') AS state_insert,
                    has_column_privilege(%s, 'mf.fund_family_association_state', 'version', 'UPDATE') AS version_update,
                    has_column_privilege(%s, 'mf.fund_family_association_state', 'tlws_mf_id', 'UPDATE') AS id_update,
                    has_table_privilege(%s, 'mf.fund_family_association_tags', 'INSERT') AS tag_insert,
                    has_table_privilege(%s, 'mf.fund_family_association_tags', 'DELETE') AS tag_delete,
                    has_table_privilege(%s, 'mf.association_tag_idempotency', 'INSERT') AS key_insert,
                    has_table_privilege(%s, 'mf.association_tag_idempotency', 'UPDATE') AS key_update,
                    roles.rolcreatedb,
                    roles.rolcreaterole,
                    roles.rolsuper,
                    roles.rolreplication,
                    roles.rolbypassrls,
                    pg_has_role(%s, 'cloudsqlsuperuser', 'member') AS cloudsqlsuperuser_member
                  FROM pg_roles AS roles
                 WHERE roles.rolname = %s
                """,
                (role,) * 12,
            ).fetchone()
        assert checks == {
            "family_select": True,
            "family_update": False,
            "state_select": True,
            "state_insert": True,
            "version_update": True,
            "id_update": False,
            "tag_insert": True,
            "tag_delete": False,
            "key_insert": True,
            "key_update": False,
            "rolcreatedb": False,
            "rolcreaterole": False,
            "rolsuper": False,
            "rolreplication": False,
            "rolbypassrls": False,
            "cloudsqlsuperuser_member": False,
        }
    finally:
        admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (dbname,),
        )
        admin.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
        admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        if not cloudsql_role_preexisting:
            admin.execute(f'DROP ROLE IF EXISTS "{cloudsql_role}"')
        admin.close()


@pytest.fixture(scope="module")
def store(pg_dsn, repo_root):
    if not pg_dsn:
        pytest.skip("MF_TEST_DSN not set")
    pytest.importorskip("psycopg")
    use_copy = False if os.environ.get("MF_TEST_NO_COPY") else None
    s = PostgresStore(pg_dsn, use_copy=use_copy)
    s.connect()
    s.apply_migrations(Path(repo_root) / "sql")

    # Safety guard: these tests are destructive. Refuse to wipe a database that
    # already holds real data unless the operator explicitly opts in — this
    # prevents pointing MF_TEST_DSN at a populated/demo/production database.
    with s.transaction() as conn, conn.cursor() as cur:
        existing = cur.execute("SELECT count(*) AS n FROM mf.funds").fetchone()["n"]
    if existing > 0 and not os.environ.get("MF_TEST_ALLOW_WIPE"):
        pytest.skip(
            f"database has {existing} funds; these tests wipe data. "
            "Point MF_TEST_DSN at a scratch DB, or set MF_TEST_ALLOW_WIPE=1"
        )

    with s.transaction() as conn, conn.cursor() as cur:
        for table in _TABLES_TO_CLEAR:
            cur.execute(f"DELETE FROM mf.{table}")
    yield s
    s.close()


@pytest.fixture
def sample_schemes(scheme_factory):
    """A small synthetic universe covering the interesting classification paths."""
    base = dict(amc="Test Mutual Fund", scheme_type="Open Ended Schemes",
                scheme_category="Equity Scheme - Large Cap")
    today = date(2024, 12, 27)
    return [
        scheme_factory(amfi_scheme_code="100001", plan_type="REGULAR", option="GROWTH",
                       scheme_name="Test Large Cap Fund - Regular Plan - Growth",
                       nav=100.0, nav_date=today, **base),
        scheme_factory(amfi_scheme_code="100002", plan_type="REGULAR", option="IDCW",
                       scheme_name="Test Large Cap Fund - Regular Plan - IDCW Option",
                       nav=95.5, nav_date=today, **base),
        scheme_factory(amfi_scheme_code="100003", plan_type="DIRECT", option="GROWTH",
                       scheme_name="Test Large Cap Fund - Direct Plan - Growth",
                       nav=110.0, nav_date=today, **base),
        scheme_factory(amfi_scheme_code="100004", plan_type="DIRECT", option="GROWTH",
                       scheme_name="Test ETF - Direct Plan - Growth",
                       nav=50.0, nav_date=today, is_etf=True,
                       **{**base, "scheme_category": "ETF - Equity"}),
        # redeemed close-ended scheme: inactive but not defunct
        scheme_factory(amfi_scheme_code="100005", plan_type="REGULAR", option="GROWTH",
                       scheme_name="Test Series 1 - Growth", nav=1234.5678,
                       nav_date=date(2021, 3, 31), isin_div_reinvestment="REDEEMED",
                       scheme_type="Close Ended Schemes", scheme_category="Income",
                       amc="Test Mutual Fund"),
        # defunct scheme
        scheme_factory(amfi_scheme_code="100006", plan_type="REGULAR", option="GROWTH",
                       scheme_name="Test OLD- Defunct Fund - Regular Plan - Growth",
                       nav=0.0, nav_date=today, is_defunct=True, **base),
        # scheme with no published NAV
        scheme_factory(amfi_scheme_code="100007", plan_type="REGULAR", option="GROWTH",
                       scheme_name="Test Never Published - Regular Plan - Growth",
                       nav=None, nav_date=None, nav_not_published=True, **base),
    ]


def test_migrations_are_idempotent(store, repo_root):
    """A fresh apply_migrations must yield the complete, post-purge schema.

    012/013 are one-way destructive purges, so re-running the *entire* set is not
    a supported operation (a real restore brings schema+data together, and a new
    DB applies each migration exactly once). What we DO guarantee is that a fresh
    apply ends in the complete schema: our own fund_family identity table present,
    every aggregator-identity column/table purged, and the key views in place.
    This is exactly the regression the compliance purge + fund_family work targets.
    """
    cur = store.connect().cursor()
    fact_cols = {r["column_name"] for r in cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='mf' AND table_name='fund_facts'").fetchall()}
    tables = {r["table_name"] for r in cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='mf'").fetchall()}
    views = {r["table_name"] for r in cur.execute(
        "SELECT table_name FROM information_schema.views "
        "WHERE table_schema='mf'").fetchall()}

    # our own identity table is present (populated by `make build-family`)
    assert "fund_family" in tables
    # aggregator identity / provenance is fully purged from fund_facts
    for purged in ("scripbox_fund_id", "groww_slug", "groww_rating",
                   "raw_payload", "fund_slug", "fund_variant",
                   "groww_return_stats", "groww_fetched_at",
                   "groww_source_mode", "groww_inherited_from"):
        assert purged not in fact_cols, f"{purged} should be purged"
    # the aggregator editorial table is gone
    assert "fund_opinions" not in tables
    # key operational views exist
    assert "v_enrichment_coverage" in views
    assert "v_fund_data_status" in views
    assert "dataset_summary" in tables

    ledger = cur.execute(
        "SELECT migration_name FROM mf.schema_migrations ORDER BY migration_name"
    ).fetchall()
    assert [row["migration_name"] for row in ledger] == sorted(DEFAULT_MIGRATIONS)
    # A second runner pass verifies checksums and performs no schema replay.
    assert store.apply_migrations(Path(repo_root) / "sql") == []


def test_snapshot_run_is_linked_to_exact_fetch_and_preserves_notes(store):
    fetch_id = store.record_fetch(
        {
            "source": "AMFI",
            "endpoint": "fixture://snapshot-run",
            "entity_kind": "LATEST_NAV",
            "entity_key": "2026-10-10",
            "acquisition": "FIXTURE",
            "content_hash": "c" * 64,
            "records_in": 1_000,
            "records_ok": 1_000,
            "records_quarantined": 0,
            "notes": {
                "snapshot_scope": "FULL",
                "snapshot_date": "2026-10-10",
                "latest_nav_date": "2026-10-11",
            },
        }
    )
    snapshot = store.record_snapshot_run(
        source_fetch_id=fetch_id,
        source="AMFI",
        snapshot_scope="FULL",
        snapshot_date=date(2026, 10, 10),
        latest_nav_date=date(2026, 10, 11),
        content_hash="c" * 64,
        records_in=1_000,
        records_ok=1_000,
        records_quarantined=0,
        distinct_amcs=25,
        distinct_categories=40,
    )
    assert snapshot["source_fetch_id"] == fetch_id
    assert store.latest_full_snapshot()["snapshot_date"] == date(2026, 10, 10)
    notes = store.connect().execute(
        "SELECT notes FROM mf.source_metadata WHERE fetch_id = %s", (fetch_id,)
    ).fetchone()["notes"]
    assert notes == {
        "snapshot_scope": "FULL",
        "snapshot_date": "2026-10-10",
        "latest_nav_date": "2026-10-11",
    }
    with store.transaction() as conn:
        conn.execute("DELETE FROM mf.source_snapshot_runs WHERE source_fetch_id = %s", (fetch_id,))
        conn.execute("DELETE FROM mf.source_metadata WHERE fetch_id = %s", (fetch_id,))


def test_load_populates_every_table(store, sample_schemes):
    res = load_parsed_amfi(store, sample_schemes, source_date=date(2024, 12, 27))
    assert res["amcs"]["inserted"] == 1
    assert res["funds"]["inserted"] == 7
    # 100007 has no NAV/date, so it is deliberately not written
    assert res["nav"]["inserted"] == 6
    assert res["flags"]["inserted"] >= 2  # REDEEMED marker + defunct scheme


def test_reload_is_idempotent(store, sample_schemes):
    """A repeated ingest must not duplicate or churn anything."""
    load_parsed_amfi(store, sample_schemes, source_date=date(2024, 12, 27))
    res = load_parsed_amfi(store, sample_schemes, source_date=date(2024, 12, 27))
    assert res["funds"]["inserted"] == 0
    assert res["funds"]["updated"] == 0
    assert res["funds"]["unchanged"] == 7
    assert res["nav"]["inserted"] == 0
    assert res["nav"]["updated"] == 0
    assert res["variants"]["inserted"] == 0
    assert res["variants"]["updated"] == 0


def test_dataset_summary_is_exact_versioned_and_request_query_is_constant_time(
    store, sample_schemes
):
    load_parsed_amfi(store, sample_schemes, source_date=date(2024, 12, 27))
    source_hash = "a" * 64

    first = store.refresh_dataset_summary(
        "integration_test",
        source_content_hash=source_hash,
    )
    result = queries.stats(store.connect())
    exact = store.connect().execute(
        """
        SELECT
            (SELECT count(*) FROM mf.funds) AS schemes_total,
            (SELECT count(*) FROM mf.funds WHERE in_scope) AS in_scope_total,
            (SELECT count(*) FROM mf.funds WHERE in_scope AND NOT is_defunct)
                AS in_scope_live,
            (SELECT count(*) FROM mf.nav_history) AS nav_rows,
            (SELECT min(nav_date) FROM mf.nav_history) AS nav_first,
            (SELECT max(nav_date) FROM mf.nav_history) AS nav_last
        """
    ).fetchone()

    for field in ("schemes_total", "in_scope_total", "in_scope_live", "nav_rows"):
        assert result[field] == exact[field]
    assert result["nav_first"] == exact["nav_first"].isoformat()
    assert result["nav_last"] == exact["nav_last"].isoformat()
    assert result["dataset_version"] == first["dataset_version"]
    assert result["source_content_hash"] == source_hash
    assert result["dataset_refreshed_at"] == first["refreshed_at"].isoformat()

    second = store.refresh_dataset_summary("integration_test_repeat")
    assert second["dataset_version"] == first["dataset_version"] + 1
    assert second["source_content_hash"] == source_hash

    plan = store.connect().execute(
        "EXPLAIN (FORMAT JSON) " + queries._STATS_SQL
    ).fetchone()["QUERY PLAN"]
    plan_text = json.dumps(plan)
    assert "dataset_summary" in plan_text
    assert "nav_history" not in plan_text


def test_dataset_summary_refresh_rolls_back_with_outer_transaction(store):
    before = store.connect().execute(
        "SELECT dataset_version FROM mf.dataset_summary WHERE singleton"
    ).fetchone()["dataset_version"]

    with pytest.raises(RuntimeError, match="force rollback"):
        with store.transaction():
            refreshed = store.refresh_dataset_summary("rollback_test")
            assert refreshed["dataset_version"] == before + 1
            raise RuntimeError("force rollback")

    after = store.connect().execute(
        "SELECT dataset_version FROM mf.dataset_summary WHERE singleton"
    ).fetchone()["dataset_version"]
    assert after == before


def test_in_scope_is_generated_correctly(store):
    """in_scope is GENERATED: Regular Plans in, Direct/ETF out."""
    cur = store.connect().cursor()
    rows = {r["amfi_scheme_code"]: r["in_scope"] for r in cur.execute(
        "SELECT amfi_scheme_code, in_scope FROM mf.funds").fetchall()}
    assert rows[100001] is True    # Regular growth
    assert rows[100002] is True    # Regular IDCW - all options are in scope
    assert rows[100003] is False   # Direct
    assert rows[100004] is False   # Direct + ETF
    assert rows[100006] is True    # Regular, even though defunct


def test_redeemed_is_inactive_but_not_defunct(store):
    cur = store.connect().cursor()
    row = cur.execute(
        "SELECT is_active, is_defunct, isin_div_reinvest FROM mf.funds "
        "WHERE amfi_scheme_code = 100005").fetchone()
    assert row["is_active"] is False
    assert row["is_defunct"] is False
    assert row["isin_div_reinvest"] is None  # marker coerced, not stored


def test_nav_is_exact_numeric(store):
    """NUMERIC(18,4) must round-trip a Decimal without float drift."""
    cur = store.connect().cursor()
    row = cur.execute(
        "SELECT nav, pg_typeof(nav)::text AS t FROM mf.nav_history "
        "WHERE amfi_scheme_code = 100005").fetchone()
    assert row["t"] == "numeric"
    assert row["nav"] == Decimal("1234.5678")
    assert isinstance(row["nav"], Decimal)


def test_rows_land_in_year_partitions(store):
    cur = store.connect().cursor()
    parts = {r["p"]: r["n"] for r in cur.execute(
        "SELECT tableoid::regclass::text AS p, count(*) AS n "
        "FROM mf.nav_history GROUP BY 1").fetchall()}
    assert any(p.endswith("y2024") for p in parts)
    assert any(p.endswith("y2021") for p in parts)
    # DEFAULT must stay empty: a row there means a date was not routed
    assert cur.execute(
        "SELECT count(*) AS n FROM mf.nav_history_default").fetchone()["n"] == 0


def test_nav_last_5y_window_is_anchored_on_data(store):
    """The view spans 5 years back from the dataset max date, not CURRENT_DATE."""
    cur = store.connect().cursor()
    span = cur.execute("SELECT max(nav_date) AS mx FROM mf.nav_history").fetchone()["mx"]
    rows = cur.execute(
        "SELECT min(nav_date) AS mn, max(nav_date) AS mx "
        "FROM mf.nav_last_5y").fetchone()
    assert rows["mx"] == span

    # The contract is "nav_date > max(nav_date) - 5 years". With sparse test data
    # the minimum is simply the earliest row inside the window, so assert the
    # boundary directly rather than assuming dense coverage.
    cutoff = cur.execute(
        "SELECT (max(nav_date) - interval '5 years')::date AS c "
        "FROM mf.nav_history").fetchone()["c"]
    assert rows["mn"] > cutoff
    # nothing outside the window leaks in
    assert cur.execute(
        "SELECT count(*) AS n FROM mf.nav_last_5y WHERE nav_date <= %s",
        (cutoff,)).fetchone()["n"] == 0

    plans = {r["plan_type"] for r in cur.execute(
        "SELECT DISTINCT plan_type FROM mf.nav_last_5y").fetchall()}
    assert plans == {"REGULAR"}


def test_nav_window_function_matches_the_view(store):
    cur = store.connect().cursor()
    view_n = cur.execute("SELECT count(*) AS n FROM mf.nav_last_5y").fetchone()["n"]
    func_n = cur.execute("SELECT count(*) AS n FROM mf.nav_window(5, NULL)").fetchone()["n"]
    assert view_n == func_n


def test_regular_direct_pairing(store):
    cur = store.connect().cursor()
    row = cur.execute(
        "SELECT group_key, regular_code, direct_code, has_direct_sibling "
        "FROM mf.fund_variants WHERE amfi_scheme_code = 100001").fetchone()
    assert row["group_key"] == "TEST LARGE CAP FUND"
    assert row["regular_code"] == 100001
    assert row["direct_code"] == 100003
    assert row["has_direct_sibling"] is True


def test_quality_flags_record_coerced_values(store):
    cur = store.connect().cursor()
    types = {r["flag_type"] for r in cur.execute(
        "SELECT flag_type FROM mf.quality_flags").fetchall()}
    assert "LIFECYCLE_ENDED" in types   # the REDEEMED marker
    assert "DEAD_SCHEME" in types       # the defunct scheme


def test_unregistered_amc_is_rejected(store, sample_schemes):
    """A funds load whose AMC is unregistered must fail loudly, not drop rows."""
    rogue = list(sample_schemes) + [
        replace(sample_schemes[0], amfi_scheme_code="999999", amc="Never Registered AMC")
    ]
    with pytest.raises(ValueError, match="absent from mf.amcs"):
        store.load_funds(fund_rows(rogue))


def test_funds_batch_mixed_codes_and_isins(store, scheme_factory):
    """Batch lookup: mixed AMFI codes + ISINs, de-dup, and not-found handling."""
    today = date(2024, 12, 27)
    base = dict(amc="Test Mutual Fund", scheme_type="Open Ended Schemes",
                scheme_category="Equity Scheme - Large Cap", nav_date=today)
    schemes = [
        scheme_factory(amfi_scheme_code="200001", plan_type="REGULAR", option="GROWTH",
                       scheme_name="Batch Alpha Fund - Regular Plan - Growth",
                       nav=100.0, isin_div_payout_or_growth="INFBTCHALPHA", **base),
        scheme_factory(amfi_scheme_code="200002", plan_type="REGULAR", option="IDCW",
                       scheme_name="Batch Beta Fund - Regular Plan - IDCW Option",
                       nav=50.0, isin_div_payout_or_growth="INFBTCHBETAP",
                       isin_div_reinvestment="INFBTCHBETAR", **base),
    ]
    load_parsed_amfi(store, schemes, source_date=today)

    # code + its own growth ISIN (same fund) + a second-column ISIN + 2 misses
    res = queries.funds_batch(
        store.connect(), ["200001", "INFBTCHALPHA", "INFBTCHBETAR", "999999", "BADISIN"])

    assert res["found"] == 2
    assert set(res["not_found"]) == {"999999", "BADISIN"}
    assert [f["amfi_scheme_code"] for f in res["funds"]] == [200001, 200002]
    # the code and its ISIN collapse to one fund, tagged with both inputs
    assert res["funds"][0]["matched_by"] == ["200001", "INFBTCHALPHA"]
    # the reinvestment ISIN (second column) still resolves
    assert res["funds"][1]["matched_by"] == ["INFBTCHBETAR"]


def test_funds_batch_returns_latest_nav(store, scheme_factory):
    """Each batch result carries the fund's latest NAV from nav_history."""
    today = date(2024, 12, 27)
    base = dict(amc="Test Mutual Fund", scheme_type="Open Ended Schemes",
                scheme_category="Equity Scheme - Large Cap")
    schemes = [
        scheme_factory(amfi_scheme_code="200010", plan_type="REGULAR", option="GROWTH",
                       scheme_name="Batch Nav Fund - Regular Plan - Growth",
                       nav=10.0, nav_date=date(2024, 12, 25),
                       isin_div_payout_or_growth="INFBTCHNAV01", **base),
        scheme_factory(amfi_scheme_code="200011", plan_type="REGULAR", option="GROWTH",
                       scheme_name="Batch Nav Fund 2 - Regular Plan - Growth",
                       nav=20.0, nav_date=date(2024, 12, 26),
                       isin_div_payout_or_growth="INFBTCHNAV02", **base),
    ]
    load_parsed_amfi(store, schemes, source_date=today)
    res = queries.funds_batch(store.connect(), ["200010", "200011"])
    by_code = {f["amfi_scheme_code"]: f for f in res["funds"]}
    assert by_code[200010]["latest_nav"] == 10.0
    assert by_code[200010]["latest_nav_date"] == "2024-12-25"
    assert by_code[200011]["latest_nav"] == 20.0


def test_category_movers_groups_by_family(store, scheme_factory):
    """category_movers: top-5 gainers/losers per broad family, in-scope only."""
    day1, day2 = date(2024, 12, 1), date(2024, 12, 27)
    base = dict(amc="Test Mutual Fund", scheme_type="Open Ended Schemes")

    def scheme(code, cat, nav, nav_date, **kw):
        return scheme_factory(amfi_scheme_code=code, plan_type="REGULAR", option="GROWTH",
                              scheme_name=f"Cat {code} - Regular Plan - Growth",
                              scheme_category=cat, nav=nav, nav_date=nav_date, **base, **kw)

    # Two NAV dates per fund so the 1m window has a prev and a latest.
    specs = [
        # code,    category,                    nav@day1, nav@day2
        ("300001", "Equity Scheme - Large Cap Fund", 100.0, 110.0),   # +10% Equity
        ("300002", "Equity Scheme - Small Cap Fund", 100.0, 90.0),    # -10% Equity
        ("300003", "Debt Scheme - Liquid Fund", 1000.0, 1001.0),      # +0.1% Debt
        ("300004", "Hybrid Scheme - Multi Asset Allocation", 100.0, 105.0),  # +5% Hybrid
        ("300005", "Index Funds - Equity Funds", 50.0, 48.0),         # -4% Index
        ("300006", "Other Scheme - Other  ETFs", 20.0, 21.0),         # +5% ETF
    ]
    load_parsed_amfi(store, [scheme(c, cat, n1, day1) for c, cat, n1, _ in specs], source_date=day1)
    load_parsed_amfi(store, [scheme(c, cat, n2, day2) for c, cat, _, n2 in specs], source_date=day2)
    # Direct-plan scheme: out of scope, must never appear.
    load_parsed_amfi(store, [
        scheme_factory(amfi_scheme_code="300007", plan_type="DIRECT", option="GROWTH",
                       scheme_name="Cat Direct Excluded - Direct Plan - Growth",
                       scheme_category="Equity Scheme - Mid Cap Fund",
                       nav=200.0, nav_date=day2, **base)], source_date=day2)

    res = queries.category_movers(store.connect(), period="1m", limit=5)
    fams = {c["category"]: c for c in res["categories"]}

    # Expected families are present (plus whatever the shared fixture loaded).
    for fam in ("Equity", "Debt", "Hybrid", "Index", "ETF"):
        assert fam in fams, fam
    g = {c["category"]: [f["amfi_scheme_code"] for f in c["gainers"]] for c in res["categories"]}
    losers = {
        c["category"]: [f["amfi_scheme_code"] for f in c["losers"]]
        for c in res["categories"]
    }
    # 300001 (+10%) is the best equity mover; 300002 (-10%) the worst.
    assert g["Equity"][0] == 300001
    assert losers["Equity"][0] == 300002
    assert g["Debt"][0] == 300003
    assert g["Hybrid"][0] == 300004
    assert losers["Index"][0] == 300005
    assert g["ETF"][0] == 300006
    # pct_change is computed, not stored
    eq_gainer = next(f for f in fams["Equity"]["gainers"] if f["amfi_scheme_code"] == 300001)
    assert eq_gainer["pct_change"] == 10.0
    # out-of-scope direct plan excluded everywhere
    for fam in res["categories"]:
        for f in fam["gainers"] + fam["losers"]:
            assert f["amfi_scheme_code"] != 300007
    # limit is honoured
    assert all(len(fam["gainers"]) <= 5 and len(fam["losers"]) <= 5 for fam in res["categories"])
    # families come back in the canonical order
    order = [c["category"] for c in res["categories"]]
    for a, b in zip(order, order[1:]):
        assert FAMILY_ORDER.index(a) <= FAMILY_ORDER.index(b)


def test_category_movers_collapses_variants(store, scheme_factory):
    """Category movers collapse only comparison-eligible variants.

    IDCW NAV change excludes distributions and is therefore not eligible for
    the ranking.  The Growth variant represents the family and ``variants``
    counts only comparison-eligible variants in the window.
    """
    day1, day2 = date(2024, 12, 1), date(2024, 12, 27)
    base = dict(amc="Test Mutual Fund", scheme_type="Open Ended Schemes",
                scheme_category="Hybrid Scheme - Arbitrage Fund")
    # Same base scheme, two plan/option variants (collapses onto one group_key).
    load_parsed_amfi(store, [
        scheme_factory(amfi_scheme_code="400001", plan_type="REGULAR", option="GROWTH",
                       scheme_name="Variant Collapsing Fund - Regular Plan - Growth",
                       nav=100.0, nav_date=day1, **base),
        scheme_factory(amfi_scheme_code="400002", plan_type="REGULAR", option="IDCW",
                       scheme_name="Variant Collapsing Fund - Regular Plan - IDCW Option",
                       nav=100.0, nav_date=day1, **base),
    ], source_date=day1)
    load_parsed_amfi(store, [
        scheme_factory(amfi_scheme_code="400001", plan_type="REGULAR", option="GROWTH",
                       scheme_name="Variant Collapsing Fund - Regular Plan - Growth",
                       nav=101.0, nav_date=day2, **base),   # +1%
        scheme_factory(amfi_scheme_code="400002", plan_type="REGULAR", option="IDCW",
                       scheme_name="Variant Collapsing Fund - Regular Plan - IDCW Option",
                       nav=102.0, nav_date=day2, **base),   # +2%
    ], source_date=day2)

    res = queries.category_movers(store.connect(), period="1m", limit=5)
    hybrid = next(c for c in res["categories"] if c["category"] == "Hybrid")
    # Small universe: with <5 funds a fund can land in BOTH the top gainers and
    # top losers lists, so de-duplicate by code across both.
    seen = {}
    for f in hybrid["gainers"] + hybrid["losers"]:
        if f["amfi_scheme_code"] in (400001, 400002):
            seen[f["amfi_scheme_code"]] = f
    # Exactly one eligible row for the scheme; the larger IDCW NAV move is
    # deliberately excluded because it is not a total-return comparison.
    assert list(seen) == [400001]
    rep = seen[400001]
    assert rep["pct_change"] == 1.0
    assert rep["variants"] == 1
    # The scheme counts once, not twice: Hybrid has exactly two distinct
    # families in this module (300004 from the grouping test + this one).
    assert hybrid["funds"] == 2
