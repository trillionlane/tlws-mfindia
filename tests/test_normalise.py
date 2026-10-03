"""Tests for name normalisation, grouping keys, and exact NAV conversion."""

from __future__ import annotations

from decimal import Decimal

import pytest

from mfdataindia.load.normalise import (
    base_scheme_key,
    fold,
    normalise_amc,
    to_decimal_nav,
)


class TestFold:
    def test_uppercases_and_collapses_punctuation(self):
        assert fold("ICICI Pru  Multi-Asset   Fund") == "ICICI PRU MULTI ASSET FUND"

    def test_strips_diacritics(self):
        assert fold("Crédit Suisse") == "CREDIT SUISSE"

    def test_empty_and_none(self):
        assert fold("") == ""
        assert fold(None) == ""

    def test_is_stable_idempotent(self):
        once = fold("SBI - Bluechip Fund!!")
        assert fold(once) == once


class TestNormaliseAmc:
    def test_drops_mutual_fund_suffix(self):
        assert normalise_amc("SBI Mutual Fund") == "SBI"

    def test_same_key_across_source_spellings(self):
        """AMFI, mfapi.in and Scripbox spell AMCs differently; they must join."""
        assert normalise_amc("ICICI Prudential Mutual Fund") == normalise_amc("ICICI Prudential")

    def test_does_not_reduce_a_bare_suffix_to_empty(self):
        # "FUND" alone must survive, otherwise the key becomes useless.
        assert normalise_amc("Fund") == "FUND"

    def test_handles_none(self):
        assert normalise_amc(None) == ""


class TestBaseSchemeKey:
    def test_collapses_regular_and_direct_variants(self):
        regular = base_scheme_key(
            "ICICI Prudential Multi Asset Fund - Regular Plan - Growth"
        )
        direct = base_scheme_key(
            "ICICI Prudential Multi Asset Fund - Direct Plan - IDCW Option"
        )
        assert regular == direct == "ICICI PRUDENTIAL MULTI ASSET FUND"

    def test_collapses_spelled_out_idcw(self):
        """AMCs often write the full legal phrase instead of the acronym."""
        a = base_scheme_key("Kotak Gilt Fund - Regular Plan - Growth")
        b = base_scheme_key(
            "Kotak Gilt Fund - Direct Plan - Monthly Payout of Income "
            "Distribution cum Capital Withdrawal Option"
        )
        assert a == b == "KOTAK GILT FUND"

    def test_preserves_theme_words_that_are_not_variant_tails(self):
        """Regression: "Growth" inside the base name must NOT be stripped.

        Stripping it would collapse genuinely different schemes onto one key.
        """
        key = base_scheme_key("Long Term Growth Fund")
        assert "GROWTH" in key
        assert key == "LONG TERM GROWTH FUND"

    def test_never_drops_the_only_segment(self):
        assert base_scheme_key("Growth") == "GROWTH"

    def test_handles_parenthesised_options(self):
        assert base_scheme_key("Axis Bluechip Fund (Growth)") == "AXIS BLUECHIP FUND"

    def test_handles_none(self):
        assert base_scheme_key(None) == ""


class TestToDecimalNav:
    def test_float_round_trips_exactly(self):
        """AMFI publishes <=4 decimals, so str(float) is lossless here."""
        assert to_decimal_nav(549.51) == Decimal("549.5100")

    def test_quantises_to_four_places(self):
        assert to_decimal_nav(Decimal("12.3456789")) == Decimal("12.3457")

    def test_accepts_string_with_thousands_separator(self):
        assert to_decimal_nav("1,234.5678") == Decimal("1234.5678")

    def test_accepts_int(self):
        assert to_decimal_nav(10) == Decimal("10.0000")

    def test_rejects_junk(self):
        assert to_decimal_nav("N.A.") is None
        assert to_decimal_nav("") is None
        assert to_decimal_nav(None) is None

    def test_rejects_non_finite(self):
        assert to_decimal_nav(Decimal("NaN")) is None
        assert to_decimal_nav(float("inf")) is None

    def test_result_is_decimal_not_float(self):
        """The whole point: NUMERIC(18,4) must never receive a float."""
        assert isinstance(to_decimal_nav(1.5), Decimal)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("102.3377", Decimal("102.3377")),
        ("0.0000", Decimal("0.0000")),
        (0.0, Decimal("0.0000")),
    ],
)
def test_to_decimal_nav_published_values(raw, expected):
    assert to_decimal_nav(raw) == expected
