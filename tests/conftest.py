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
