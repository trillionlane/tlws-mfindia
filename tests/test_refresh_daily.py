from __future__ import annotations

import importlib.util
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from mfdataindia.ingest.amfi_navall import AmfiScheme, ParseReport


def _module():
    path = Path(__file__).parents[1] / "scripts" / "refresh_daily.py"
    spec = importlib.util.spec_from_file_location("refresh_daily", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _feed(feed_date: date, count: int = 1_000):
    schemes = [
        AmfiScheme(
            amfi_scheme_code=str(100_000 + index),
            scheme_name=f"Fund {index}",
            nav=10.0,
            nav_date=feed_date,
            scheme_type="OPEN_ENDED",
            scheme_category="Equity",
            amc=f"AMC {index % 25}",
            isin_div_payout_or_growth=None,
            isin_div_reinvestment=None,
            plan_type="REGULAR",
            in_scope=True,
        )
        for index in range(count)
    ]
    report = ParseReport(
        total_lines=count,
        data_rows=count,
        format="NAVALL_8COL",
        distinct_amcs=25,
        plan_types=Counter({"REGULAR": count}),
    )
    return schemes, report


def test_validate_feed_accepts_recent_complete_navall():
    refresh = _module()
    schemes, report = _feed(date(2026, 10, 7))
    assert refresh.validate_feed(schemes, report, as_of=date(2026, 10, 8)) == date(2026, 10, 7)


@pytest.mark.parametrize("feed_date", [date(2026, 9, 30), date(2026, 10, 9)])
def test_validate_feed_rejects_stale_or_future_dates(feed_date):
    refresh = _module()
    schemes, report = _feed(feed_date)
    with pytest.raises(RuntimeError):
        refresh.validate_feed(schemes, report, as_of=date(2026, 10, 8))


def test_validate_feed_rejects_truncated_payload():
    refresh = _module()
    schemes, report = _feed(date(2026, 10, 8), count=999)
    with pytest.raises(RuntimeError):
        refresh.validate_feed(schemes, report, as_of=date(2026, 10, 8))


def test_refresh_uses_allowed_latest_nav_provenance_kind():
    refresh = _module()
    assert refresh.SOURCE_ENTITY_KIND == "LATEST_NAV"
