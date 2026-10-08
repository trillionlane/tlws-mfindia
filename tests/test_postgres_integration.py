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
from fastapi.testclient import TestClient

from mfdataindia.api import queries
from mfdataindia.api.app import create_app
from mfdataindia.api.queries import FAMILY_ORDER
from mfdataindia.load.amfi_to_store import fund_rows, load_parsed_amfi
from mfdataindia.store.postgres import DEFAULT_MIGRATIONS, PostgresStore

pytestmark = pytest.mark.postgres

_TABLES_TO_CLEAR = (
    "ingest_checkpoints", "quality_flags", "fund_variants", "nav_history", "funds", "amcs",
)


def test_api_health_uses_the_postgres_pool(pg_dsn):
    if not pg_dsn:
        pytest.skip("MF_TEST_DSN not set")
    with TestClient(create_app(pg_dsn)) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "PostgreSQL 18" in response.json()["db"]


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

    ledger = cur.execute(
        "SELECT migration_name FROM mf.schema_migrations ORDER BY migration_name"
    ).fetchall()
    assert [row["migration_name"] for row in ledger] == sorted(DEFAULT_MIGRATIONS)
    # A second runner pass verifies checksums and performs no schema replay.
    assert store.apply_migrations(Path(repo_root) / "sql") == []


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
    """GROWTH + IDCW variants of one scheme collapse to a single row.

    The variant with the biggest absolute move is the representative; its
    ``variants`` count reports how many plan/option variants were in the window.
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
    # Exactly one row for the scheme (collapsed), not one per variant
    assert list(seen) == [400002]
    rep = seen[400002]
    # Biggest absolute move (the +2% IDCW variant) represents the family
    assert rep["pct_change"] == 2.0
    assert rep["variants"] == 2
    # The scheme counts once, not twice: Hybrid has exactly two distinct
    # families in this module (300004 from the grouping test + this one).
    assert hybrid["funds"] == 2
