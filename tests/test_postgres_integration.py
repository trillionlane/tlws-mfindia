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

import os
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from mfdataindia.load.amfi_to_store import fund_rows, load_parsed_amfi
from mfdataindia.store.postgres import PostgresStore

pytestmark = pytest.mark.postgres

_TABLES_TO_CLEAR = (
    "ingest_checkpoints", "quality_flags", "fund_variants", "nav_history", "funds", "amcs",
)


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
    """Re-applying every migration must not error (IF NOT EXISTS everywhere)."""
    assert store.apply_migrations(Path(repo_root) / "sql") == [
        "001_core_schema.sql", "002_nav_and_views.sql", "003_enrichment_recon.sql",
    ]


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
