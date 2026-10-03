"""Tests for the bulk NAV-history report parser (the 5-year backfill mechanism)."""

from __future__ import annotations

from datetime import date

from mfdataindia.ingest.amfi_nav_history import parse_nav_history_report

SAMPLE = """Scheme Code;NAV Name;Plan;Option;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;Net Asset Value;Date

Open Ended Schemes ( Equity Scheme - Large Cap Fund )

Test Mutual Fund

100001;Test Large Cap Fund;Regular Plan;Growth;INF000000001;-;15.93;01-Jan-2024
100001;Test Large Cap Fund;Regular Plan;Growth;INF000000001;-;15.85;02-Jan-2024
100001;Test Large Cap Fund;Regular Plan;Growth;INF000000001;-;15.82;03-Jan-2024
100002;Test Large Cap Fund;Direct Plan;Growth;-;-;110.50;01-Jan-2024
100003;Test Unclaimed Fund;;;-;-;N.A.;01-Jan-2024
"""


class TestHistoryParser:
    def test_parses_per_date_rows(self):
        rows, report = parse_nav_history_report(SAMPLE)
        assert report.format == "NAVHIST_8COL"
        assert report.quarantined == 0
        # 3 dates of code 100001 + 1 of 100002 + 1 of 100003 (N.A.)
        assert len(rows) == 5

    def test_scheme_repeats_across_dates(self):
        rows, _ = parse_nav_history_report(SAMPLE)
        series = {r.nav_date: r.nav for r in rows if r.amfi_scheme_code == "100001"}
        assert series == {date(2024, 1, 1): 15.93,
                          date(2024, 1, 2): 15.85,
                          date(2024, 1, 3): 15.82}

    def test_plan_and_option_from_column(self):
        rows, _ = parse_nav_history_report(SAMPLE)
        by_code = {r.amfi_scheme_code: r for r in rows}
        assert by_code["100001"].plan_type == "REGULAR"
        assert by_code["100001"].option == "GROWTH"
        assert by_code["100001"].plan_source == "COLUMN"
        assert by_code["100002"].plan_type == "DIRECT"

    def test_na_nav_is_not_published_not_an_error(self):
        rows, report = parse_nav_history_report(SAMPLE)
        na = next(r for r in rows if r.amfi_scheme_code == "100003")
        assert na.nav is None
        assert na.nav_not_published is True
        assert report.nav_not_published == 1
        assert report.unparsed_nav == 0

    def test_spaced_paren_section_header_parsed(self):
        rows, _ = parse_nav_history_report(SAMPLE)
        assert rows[0].scheme_type == "Open Ended Schemes"
        assert rows[0].scheme_category == "Equity Scheme - Large Cap Fund"
        assert rows[0].amc == "Test Mutual Fund"

    def test_history_rows_never_decide_scope(self):
        """Identity/scope is mf.funds' job (from NAVAll.txt), not the history feed."""
        rows, _ = parse_nav_history_report(SAMPLE)
        assert all(r.in_scope is False for r in rows)
