"""Tests for the AMFI -> mf.* row mapping.

Several of these encode real edge cases found in the 27-Dec-2024 AMFI snapshot;
they are regression guards so the findings are not lost.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from mfdataindia.load.amfi_to_store import (
    PERIODICITY_MAP,
    REDEEMED_MARKER,
    amc_names,
    clean_isin,
    fund_rows,
    is_redeemed,
    map_periodicity,
    map_scheme_type,
    nav_rows,
    quality_flag_rows,
    variant_rows,
)
from mfdataindia.store.postgres import FUND_COLUMNS


class TestMapSchemeType:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Open Ended Schemes", "OPEN_ENDED"),
            ("Close Ended Schemes", "CLOSE_ENDED"),
            ("Interval Fund Schemes", "INTERVAL"),
            ("OPEN_ENDED", "OPEN_ENDED"),
            (None, "OPEN_ENDED"),
        ],
    )
    def test_known_wording(self, raw, expected):
        assert map_scheme_type(raw) == expected

    def test_result_always_satisfies_the_schema_check(self):
        allowed = {"OPEN_ENDED", "CLOSE_ENDED", "INTERVAL"}
        for raw in ("Open Ended Schemes", "weird", "", None, "Interval"):
            assert map_scheme_type(raw) in allowed


class TestMapPeriodicity:
    def test_half_yearly_space_form_is_mapped(self):
        """Regression: AMFI writes "HALF YEARLY"; the schema wants HALF_YEARLY.

        Without this the whole load aborts on funds_periodicity_check.
        """
        assert map_periodicity("HALF YEARLY") == "HALF_YEARLY"

    def test_half_yearly_hyphen_form_is_mapped(self):
        assert map_periodicity("Half-Yearly") == "HALF_YEARLY"

    def test_none_passes_through(self):
        assert map_periodicity(None) is None
        assert map_periodicity("") is None

    @pytest.mark.parametrize("token", list(PERIODICITY_MAP))
    def test_every_mapped_value_satisfies_the_schema_check(self, token):
        allowed = {
            "DAILY", "WEEKLY", "FORTNIGHTLY", "MONTHLY", "QUARTERLY",
            "HALF_YEARLY", "ANNUAL", "PERIODIC",
        }
        assert map_periodicity(token) in allowed


class TestCleanIsin:
    def test_accepts_a_valid_isin(self):
        assert clean_isin("INF109K01761") == "INF109K01761"

    def test_rejects_the_redeemed_marker(self):
        """Regression: AMFI writes the literal REDEEMED in the ISIN column."""
        assert clean_isin(REDEEMED_MARKER) is None
        assert clean_isin("REDEEMED    ") is None

    @pytest.mark.parametrize(
        "raw",
        [
            "INF174K1TA2",   # 11 chars - truncated, seen in the real snapshot
            "HDFCNIVODG",    # 10 chars - seen in the real snapshot
            "",
            None,
            "-",
        ],
    )
    def test_rejects_malformed_and_empty(self, raw):
        assert clean_isin(raw) is None


class TestIsRedeemed:
    def test_detects_marker_in_column_b(self, scheme_factory):
        s = scheme_factory(isin_div_reinvestment="REDEEMED")
        assert is_redeemed(s) is True

    def test_false_for_normal_rows(self, scheme_factory):
        assert is_redeemed(scheme_factory()) is False


class TestFundRows:
    def test_row_width_matches_fund_columns(self, scheme_factory):
        row = next(fund_rows([scheme_factory()]))
        assert len(row) == len(FUND_COLUMNS)

    def test_redeemed_scheme_is_marked_inactive_but_not_defunct(self, scheme_factory):
        """A redeemed close-ended scheme completed its tenure; it did not fail."""
        s = scheme_factory(isin_div_reinvestment="REDEEMED", is_defunct=False)
        fields = dict(zip(FUND_COLUMNS, next(fund_rows([s]))))
        assert fields["is_active"] is False
        assert fields["is_defunct"] is False
        # the marker must not leak into the ISIN column
        assert fields["isin_div_reinvest"] is None
        assert fields["isin_growth_or_div_payout"] == "INF109K01761"

    def test_defunct_scheme_is_inactive(self, scheme_factory):
        fields = dict(zip(FUND_COLUMNS, next(fund_rows([scheme_factory(is_defunct=True)]))))
        assert fields["is_active"] is False
        assert fields["is_defunct"] is True

    def test_non_numeric_code_is_skipped(self, scheme_factory):
        assert list(fund_rows([scheme_factory(amfi_scheme_code="JUNK")])) == []

    def test_scheme_type_is_normalised(self, scheme_factory):
        s = scheme_factory(scheme_type="Close Ended Schemes")
        assert dict(zip(FUND_COLUMNS, next(fund_rows([s]))))["scheme_type"] == "CLOSE_ENDED"


class TestNavRows:
    def test_yields_exact_decimal_nav(self, scheme_factory):
        code, d, nav, source, verified = next(nav_rows([scheme_factory()]))
        assert code == 120503
        assert d == date(2024, 12, 27)
        assert str(nav) == "549.5100"
        assert source == "AMFI"
        assert verified is False

    def test_skips_rows_without_a_nav(self, scheme_factory):
        """N.A. means never published; writing 0 would fabricate a price."""
        s = scheme_factory(nav=None, nav_not_published=True)
        assert list(nav_rows([s])) == []

    def test_skips_rows_without_a_date(self, scheme_factory):
        assert list(nav_rows([scheme_factory(nav_date=None)])) == []


class TestQualityFlagRows:
    def test_redeemed_becomes_lifecycle_ended_info(self, scheme_factory):
        s = scheme_factory(amfi_scheme_code="129286", isin_div_reinvestment="REDEEMED")
        flags = list(quality_flag_rows([s], include_defunct=False))
        assert len(flags) == 1
        code, _nav_date, flag_type, severity, _msg, details, _src = flags[0]
        assert code == 129286
        assert flag_type == "LIFECYCLE_ENDED"
        assert severity == "INFO"
        assert json.loads(details)["raw_value"] == "REDEEMED"

    def test_malformed_isin_becomes_isin_missing_warn(self, scheme_factory):
        flags = list(quality_flag_rows(
            [scheme_factory(isin_div_reinvestment="HDFCNIVODG")], include_defunct=False
        ))
        assert len(flags) == 1
        assert flags[0][2] == "ISIN_MISSING"
        assert flags[0][3] == "WARN"
        assert json.loads(flags[0][5])["length"] == 10

    def test_defunct_scheme_is_reported(self, scheme_factory):
        flags = list(quality_flag_rows([scheme_factory(is_defunct=True)]))
        assert any(f[2] == "DEAD_SCHEME" for f in flags)

    def test_clean_row_produces_no_flags(self, scheme_factory):
        assert list(quality_flag_rows([scheme_factory()])) == []


class TestAmcNames:
    def test_dedupes_and_sorts(self, scheme_factory):
        rows = [
            scheme_factory(amc="SBI Mutual Fund"),
            scheme_factory(amc="SBI Mutual Fund"),
            scheme_factory(amc="Axis Mutual Fund"),
            scheme_factory(amc=None),
        ]
        assert amc_names(rows) == ["Axis Mutual Fund", "SBI Mutual Fund"]


class TestVariantRows:
    def test_pairs_regular_and_direct_siblings(self, scheme_factory):
        regular = scheme_factory(
            amfi_scheme_code="120503",
            scheme_name="ICICI Pru Multi Asset Fund - Regular Plan - Growth",
            plan_type="REGULAR",
        )
        direct = scheme_factory(
            amfi_scheme_code="120504",
            scheme_name="ICICI Pru Multi Asset Fund - Direct Plan - Growth",
            plan_type="DIRECT",
        )
        rows = {r[0]: r for r in variant_rows([regular, direct])}
        assert set(rows) == {120503, 120504}
        for _code, (_c, gkey, _n, reg, dir_, paired) in rows.items():
            assert gkey == "ICICI PRU MULTI ASSET FUND"
            assert reg == 120503
            assert dir_ == 120504
            assert paired is True

    def test_different_amcs_do_not_pair(self, scheme_factory):
        """Guards against base-key collisions across fund houses."""
        a = scheme_factory(
            amfi_scheme_code="1", amc="AMC One",
            scheme_name="Bluechip Fund - Regular Plan - Growth", plan_type="REGULAR",
        )
        b = scheme_factory(
            amfi_scheme_code="2", amc="AMC Two",
            scheme_name="Bluechip Fund - Direct Plan - Growth", plan_type="DIRECT",
        )
        rows = {r[0]: r for r in variant_rows([a, b])}
        assert rows[1][5] is False
        assert rows[2][5] is False
