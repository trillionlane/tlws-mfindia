from __future__ import annotations

import importlib.util
import json
from collections import Counter
from datetime import date, timedelta
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


def _scheme(index: int, nav_date: date, *, category: str | None = "Equity") -> AmfiScheme:
    return AmfiScheme(
        amfi_scheme_code=str(100_000 + index),
        scheme_name=f"Fund {index}",
        nav=10.0,
        nav_date=nav_date,
        scheme_type="OPEN_ENDED",
        scheme_category=category,
        amc=f"AMC {index % 25}",
        isin_div_payout_or_growth=None,
        isin_div_reinvestment=None,
        plan_type="REGULAR",
        in_scope=True,
    )


def _feed(feed_date: date, count: int = 1_000):
    schemes = [_scheme(index, feed_date) for index in range(count)]
    report = ParseReport(
        total_lines=count,
        data_rows=count,
        format="NAVALL_8COL",
        distinct_amcs=25,
        distinct_categories=40,
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


def test_validate_feed_accepts_one_day_ahead_liquid_and_overnight_rows():
    refresh = _module()
    as_of = date(2026, 10, 10)
    schemes, report = _feed(as_of - timedelta(days=1))
    schemes.extend(
        [
            _scheme(
                1_001,
                as_of + timedelta(days=1),
                category="Debt Scheme - Liquid Fund",
            ),
            _scheme(
                1_002,
                as_of + timedelta(days=1),
                category="Income/Debt Oriented Schemes - Overnight Fund",
            ),
        ]
    )
    report.data_rows = len(schemes)

    assert refresh.validate_feed(schemes, report, as_of=as_of) == as_of + timedelta(days=1)


@pytest.mark.parametrize(
    ("future_days", "category"),
    [
        (1, "Equity Scheme - Large Cap Fund"),
        (2, "Debt Scheme - Liquid Fund"),
        (2, "Debt Scheme - Overnight Fund"),
        (1, "Debt Scheme - Non-Liquid Fund"),
        (1, None),
    ],
)
def test_validate_feed_rejects_unapproved_future_rows(future_days, category):
    refresh = _module()
    as_of = date(2026, 10, 10)
    schemes, report = _feed(as_of - timedelta(days=1))
    schemes.append(
        _scheme(1_001, as_of + timedelta(days=future_days), category=category)
    )
    report.data_rows = len(schemes)

    with pytest.raises(RuntimeError, match="disallowed future NAV rows"):
        refresh.validate_feed(schemes, report, as_of=as_of)


def test_validate_feed_rejects_feed_with_only_next_day_rows():
    refresh = _module()
    as_of = date(2026, 10, 10)
    schemes, report = _feed(as_of + timedelta(days=1))
    for scheme in schemes:
        scheme.scheme_category = "Debt Scheme - Liquid Fund"

    with pytest.raises(RuntimeError, match="no current or historical NAV dates"):
        refresh.validate_feed(schemes, report, as_of=as_of)


def test_validate_feed_rejects_truncated_payload():
    refresh = _module()
    schemes, report = _feed(date(2026, 10, 8), count=999)
    with pytest.raises(RuntimeError):
        refresh.validate_feed(schemes, report, as_of=date(2026, 10, 8))


def test_refresh_uses_allowed_latest_nav_provenance_kind():
    refresh = _module()
    assert refresh.SOURCE_ENTITY_KIND == "LATEST_NAV"


def test_snapshot_scope_defaults_full_and_rejects_unknown(monkeypatch):
    refresh = _module()
    monkeypatch.delenv("MFDATAINDIA_SNAPSHOT_SCOPE", raising=False)
    assert refresh.snapshot_scope() == "FULL"
    monkeypatch.setenv("MFDATAINDIA_SNAPSHOT_SCOPE", "partial")
    assert refresh.snapshot_scope() == "PARTIAL"
    monkeypatch.setenv("MFDATAINDIA_SNAPSHOT_SCOPE", "incremental")
    with pytest.raises(RuntimeError, match="FULL or PARTIAL"):
        refresh.snapshot_scope()


def test_first_full_snapshot_is_guarded_against_restored_manifest():
    refresh = _module()
    schemes, report = _feed(date(2026, 10, 10), count=9_100)
    result = refresh.validate_full_snapshot_completeness(
        schemes,
        report,
        before={"funds": 10_000, "amcs": 25, "categories": 40},
        previous_full=None,
    )
    assert result["records_ok"] == 9_100
    assert result["baseline_ratio"] == 0.90


def test_partial_payload_cannot_be_accepted_as_next_full_snapshot():
    refresh = _module()
    schemes, report = _feed(date(2026, 10, 10), count=1_500)
    report.distinct_amcs = 20
    report.distinct_categories = 25
    with pytest.raises(RuntimeError, match="failed completeness guard"):
        refresh.validate_full_snapshot_completeness(
            schemes,
            report,
            before={"funds": 14_000, "amcs": 45, "categories": 80},
            previous_full={"records_ok": 14_000, "distinct_amcs": 45, "distinct_categories": 80},
        )


def test_next_full_snapshot_uses_tighter_previous_full_baseline():
    refresh = _module()
    schemes, report = _feed(date(2026, 10, 10), count=9_700)
    with pytest.raises(RuntimeError, match="records_ok"):
        refresh.validate_full_snapshot_completeness(
            schemes,
            report,
            before={"funds": 10_000, "amcs": 25, "categories": 40},
            previous_full={"records_ok": 10_000, "distinct_amcs": 25, "distinct_categories": 40},
        )


def test_parse_report_payload_is_json_safe():
    refresh = _module()
    report = ParseReport(
        data_rows=3,
        plan_class_source=Counter({"COLUMN": 3}),
        option_class_source=Counter({"COLUMN": 2, "NAME": 1}),
        quarantine_reasons=Counter({"bad_row": 1}),
        plan_types=Counter({"REGULAR": 2, "DIRECT": 1}),
        options=Counter({"GROWTH": 3}),
        scheme_types=Counter({"Open Ended Schemes": 3}),
    )

    payload = refresh.parse_report_payload(report)

    assert payload["plan_types"] == {"REGULAR": 2, "DIRECT": 1}
    assert payload["option_class_source"] == {"COLUMN": 2, "NAME": 1}
    assert json.loads(json.dumps(payload, sort_keys=True))["options"] == {"GROWTH": 3}


def test_validate_dataset_summary_accepts_exact_manifest():
    refresh = _module()
    source_hash = "a" * 64
    manifest = {
        "funds": 14_369,
        "in_scope_total": 4_345,
        "in_scope_live": 4_291,
        "amcs": 55,
        "categories": 101,
        "nav_rows": 4_219_608,
        "nav_min_date": date(2008, 10, 2),
        "nav_max_date": date(2026, 10, 7),
        "enrichment_pct": 98.79,
    }
    summary = {
        "schemes_total": 14_369,
        "in_scope_total": 4_345,
        "in_scope_live": 4_291,
        "amcs": 55,
        "categories": 101,
        "nav_rows": 4_219_608,
        "nav_first": date(2008, 10, 2),
        "nav_last": date(2026, 10, 7),
        "enrichment_pct": 98.79,
        "source_content_hash": source_hash,
    }

    refresh.validate_dataset_summary(summary, manifest, source_content_hash=source_hash)


def test_validate_dataset_summary_rejects_stale_row_count():
    refresh = _module()
    source_hash = "b" * 64
    manifest = {
        "funds": 10,
        "in_scope_total": 9,
        "in_scope_live": 8,
        "amcs": 2,
        "categories": 3,
        "nav_rows": 101,
        "nav_min_date": date(2024, 1, 1),
        "nav_max_date": date(2024, 1, 2),
        "enrichment_pct": 75.0,
    }
    summary = {
        "schemes_total": 10,
        "in_scope_total": 9,
        "in_scope_live": 8,
        "amcs": 2,
        "categories": 3,
        "nav_rows": 100,
        "nav_first": date(2024, 1, 1),
        "nav_last": date(2024, 1, 2),
        "enrichment_pct": 75.0,
        "source_content_hash": source_hash,
    }

    with pytest.raises(RuntimeError, match="dataset summary mismatch"):
        refresh.validate_dataset_summary(summary, manifest, source_content_hash=source_hash)
