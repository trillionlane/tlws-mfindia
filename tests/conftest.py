"""Shared fixtures for MFDataIndia tests."""

from __future__ import annotations

import os
from datetime import date

import pytest

from mfdataindia.ingest.amfi_navall import AmfiScheme


def make_scheme(**overrides) -> AmfiScheme:
    """Build a representative AmfiScheme; override any field per test."""
    base = dict(
        amfi_scheme_code="120503",
        scheme_name="ICICI Prudential Multi Asset Fund - Regular Plan - Growth",
        nav=549.5100,
        nav_date=date(2024, 12, 27),
        scheme_type="Open Ended Schemes",
        scheme_category="Hybrid Scheme - Multi Asset Allocation",
        amc="ICICI Prudential Mutual Fund",
        isin_div_payout_or_growth="INF109K01761",
        isin_div_reinvestment="INF109K01779",
        plan_type="REGULAR",
        option="GROWTH",
        periodicity=None,
        is_etf=False,
        is_defunct=False,
        nav_not_published=False,
        in_scope=True,
    )
    base.update(overrides)
    return AmfiScheme(**base)


@pytest.fixture
def scheme_factory():
    return make_scheme


@pytest.fixture(scope="session")
def pg_dsn() -> str | None:
    """Live PostgreSQL DSN for integration tests; None disables them."""
    return os.environ.get("MF_TEST_DSN")


@pytest.fixture(scope="session")
def repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def certify_comparison_fixture(conn):
    """Establish synthetic full-feed/audit evidence for ranking test fixtures."""
    from mfdataindia.audit import nav_integrity

    ref = conn.execute("SELECT max(nav_date) AS day FROM mf.nav_history").fetchone()["day"]
    conn.execute("UPDATE mf.funds SET last_seen_in_source=%s", (ref,))
    fetch = conn.execute(
        "INSERT INTO mf.source_metadata(source,endpoint,entity_kind,entity_key,content_hash) "
        "VALUES('AMFI','FIXTURE_ONLY','LATEST_NAV','comparison-fixture',%s) RETURNING fetch_id",
        ("e" * 64,),
    ).fetchone()["fetch_id"]
    conn.execute(
        "INSERT INTO mf.source_snapshot_runs(source_fetch_id,source,snapshot_scope,snapshot_date,"
        "latest_nav_date,content_hash,records_in,records_ok,records_quarantined,distinct_amcs,distinct_categories) "
        "VALUES(%s,'AMFI','FULL',%s,%s,%s,1,1,0,1,1)", (fetch, ref, ref, "e" * 64),
    )
    conn.execute("SELECT mf.refresh_dataset_summary('synthetic_comparison_fixture')")
    nav_integrity.assess(conn)
