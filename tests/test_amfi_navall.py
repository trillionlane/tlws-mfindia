"""Tests for the NAVAll.txt parser: both file layouts and the plan/option rules.

These encode the format change AMFI made — the current feed added explicit
``Plan``/``Option`` columns (8 columns) while the archived feed embeds them in the
scheme name (6 columns) — and the scope rule that depends on it.
"""

from __future__ import annotations

from mfdataindia.ingest.amfi_navall import (
    classify_plan_explicit,
    parse_navall,
    resolve_option,
    resolve_plan,
)

# --- minimal fixtures for both layouts ---------------------------------------

LEGACY_6COL = """Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date

Open Ended Schemes(Equity Scheme - Large Cap Fund)

Test Mutual Fund

100001;INF000000001;-;Test Large Cap Fund - Regular Plan - Growth;100.5;27-Dec-2024
100002;-;-;Test Large Cap Fund - Direct Plan - IDCW Option;110.5;27-Dec-2024
100003;-;-;Test Unlabelled Fund;10.0;27-Dec-2024
"""

CURRENT_8COL = """Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date

Open Ended Schemes ( Equity Scheme - Large Cap Fund )

Test Mutual Fund

100001;INF000000001;-;Test Large Cap Fund;Regular Plan;Growth;100.5;01-Oct-2026
100002;-;-;Test Large Cap Fund;Direct Plan;IDCW;110.5;01-Oct-2026
100003;-;-;Test Mystery Fund;;;10.0;01-Oct-2026
"""


class TestFormatDetection:
    def test_legacy_layout_detected(self):
        schemes, report = parse_navall(LEGACY_6COL)
        assert report.format == "NAVALL_6COL"
        assert len(schemes) == 3
        assert report.quarantined == 0

    def test_current_layout_detected(self):
        schemes, report = parse_navall(CURRENT_8COL)
        assert report.format == "NAVALL_8COL"
        assert len(schemes) == 3
        assert report.quarantined == 0

    def test_mixed_layout_does_not_crash(self):
        """A file containing both row shapes parses both without quarantining."""
        mixed = (
            "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date\n\n"
            "Open Ended Schemes(Equity Scheme - Large Cap Fund)\n\nTest Mutual Fund\n\n"
            "100001;-;-;Old Format Fund - Regular Plan - Growth;10.0;27-Dec-2024\n"
            "100002;-;-;New Format Fund;Regular Plan;Growth;10.0;01-Oct-2026\n"
        )
        schemes, report = parse_navall(mixed)
        assert len(schemes) == 2
        assert report.quarantined == 0


class TestExplicitColumnClassification:
    def test_plan_from_column(self):
        assert classify_plan_explicit("Regular Plan") == "REGULAR"
        assert classify_plan_explicit("Direct Plan") == "DIRECT"
        assert classify_plan_explicit("regular") == "REGULAR"  # case-insensitive

    def test_plan_blank_or_unknown_returns_none(self):
        assert classify_plan_explicit("") is None
        assert classify_plan_explicit(None) is None
        assert classify_plan_explicit("Peculiar Plan") is None

    def test_resolve_plan_prefers_column(self):
        # name says Regular but the column is authoritative and says Direct
        plan, src = resolve_plan("Some Fund - Regular Plan", "Direct Plan")
        assert plan == "DIRECT"
        assert src == "COLUMN"

    def test_resolve_plan_blank_column_marks_source(self):
        plan, src = resolve_plan("Test Mystery Fund", "")
        assert plan == "UNLABELLED"
        assert src == "COLUMN_BLANK"

    def test_resolve_plan_no_column_uses_name(self):
        plan, src = resolve_plan("Test Unlabelled Fund", None)
        assert plan == "UNLABELLED"
        assert src == "NAME"

    def test_resolve_plan_unrecognised_column_falls_back_to_name(self):
        plan, src = resolve_plan("Some Fund - Direct Plan - Growth", "Peculiar Plan")
        assert plan == "DIRECT"
        assert src == "COLUMN_UNRECOGNISED"

    def test_resolve_option_from_column(self):
        option, periodicity, src = resolve_option("Some Fund", "Monthly IDCW")
        assert option == "IDCW"
        assert periodicity == "MONTHLY"
        assert src == "COLUMN"

    def test_resolve_option_blank_column_uses_name(self):
        option, periodicity, src = resolve_option("Some Fund - Quarterly IDCW Option", "")
        assert option == "IDCW"
        assert periodicity == "QUARTERLY"
        assert src == "NAME"


class TestScopeRule:
    def test_legacy_unlabelled_is_in_scope(self):
        """Legacy feed has no Plan column, so UNLABELLED-by-name means a Regular plan."""
        schemes, _ = parse_navall(LEGACY_6COL)
        by_code = {s.amfi_scheme_code: s for s in schemes}
        assert by_code["100001"].in_scope is True     # Regular
        assert by_code["100002"].in_scope is False    # Direct
        assert by_code["100003"].in_scope is True     # UNLABELLED via name -> in scope

    def test_current_blank_plan_column_is_out_of_scope(self):
        """Current feed: a blank Plan column means plan unknown, not Regular."""
        schemes, _ = parse_navall(CURRENT_8COL)
        by_code = {s.amfi_scheme_code: s for s in schemes}
        assert by_code["100001"].in_scope is True     # Regular (explicit)
        assert by_code["100002"].in_scope is False    # Direct (explicit)
        assert by_code["100003"].in_scope is False    # blank plan -> excluded
        assert by_code["100003"].plan_type == "UNLABELLED"
        assert "plan_column_blank" in by_code["100003"].warnings

    def test_regular_only_filter(self):
        schemes, _ = parse_navall(LEGACY_6COL, regular_only=True)
        assert {s.plan_type for s in schemes} == {"REGULAR", "UNLABELLED"}
        assert all(s.in_scope for s in schemes)
