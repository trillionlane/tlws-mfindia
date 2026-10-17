"""Synthetic safety mechanics; real two-period issuer certification is separate."""

from copy import deepcopy
import hashlib

from mfdataindia.enrichment import absl_expansion as absl

NAME = "Aditya Birla Sun Life Synthetic Fund"
ISIN = "INF209K01AJ8"
IDENTITY = {
    "code": 100001,
    "amc": absl.AMC,
    "scheme_name": NAME,
    "plan": "REGULAR",
    "option": "GROWTH",
    "isins": [ISIN],
}
DETAIL = """Fund Details
Benchmark Name Nifty 500
TRI
NAV as on September 30, 2026
AUM as on September 30, 2026 12,345.67 Cr
AAUM as on September 30, 2026 99,999.99 Cr
Name of Fund Managers
• Ms. Synthetic Manager
• Mr. Other Manager
Total Experience 10 / 20
Expense Ratio
Regular Plan: BER 1.25%
Direct Plan: BER 0.50%
Aditya Birla Sun Life Synthetic Fund
Type of Scheme: Synthetic fixture, not an official disclosure
"""
CHANGES = """Aditya Birla Sun Life Synthetic Fund
September 2026
Changes in Risk-o-Meter of scheme
Scheme Riskometer High Very High
Changes in Expense Ratio of the scheme
Regular 1.24% 1.25%
Direct 0.49% 0.50%
"""


def page(text, number):
    return {"page": number, "text": text, "text_hash": hashlib.sha256(text.encode()).hexdigest()}


def summary(name=NAME, suffix="Regular Plan Growth", code=100001, isin=ISIN):
    return {
        "fund_name": name,
        "label": name + " - " + suffix,
        "code": code,
        "isin": isin,
        "page": 1,
        "text_hash": "a" * 64,
        "source_hash": "b" * 64,
    }


def details(detail=DETAIL, changes=CHANGES):
    return absl.detail_pages(
        {"source_hash": "b" * 64, "pages": [page(detail, 1), page(changes, 2)]}
    )


def test_exact_code_isin_fields_units_and_current_risk_column():
    row = absl.derive_one(IDENTITY, [summary()], details(), as_of="2026-09-30")
    assert row["status"] == "usable"
    fields = row["fields"]
    assert fields["benchmark_name"]["value"] == "Nifty 500 TRI"
    assert fields["fund_managers"]["value"] == ["Ms. Synthetic Manager", "Mr. Other Manager"]
    assert fields["aum"]["value"] == "12345.67"
    assert fields["aum"]["field_effective_date"] == "2026-09-30"
    assert fields["base_expense_ratio"]["value"] == "1.25"
    assert fields["risk_level"]["value"] == "Very High"
    assert fields["fund_managers"]["field_effective_date"] is None


def test_option_comes_from_variant_suffix_not_dividend_yield_fund_name():
    name = "Aditya Birla Sun Life Dividend Yield Fund"
    ident = {**IDENTITY, "scheme_name": name}
    matched, gap = absl.bridge(ident, [summary(name=name)])
    assert matched and gap is None
    bad = summary(name=name, suffix="Regular IDCW")
    assert absl.bridge(ident, [bad])[1] == "issuer_option_conflict"


def test_regular_fund_name_is_not_regular_plan_evidence():
    name = "Aditya Birla Sun Life Regular Savings Fund"
    ident = {**IDENTITY, "scheme_name": name}
    row = absl.derive_one(
        ident,
        [summary(name=name, suffix="Growth")],
        details(DETAIL.replace(NAME, name), CHANGES.replace(NAME, name)),
        as_of="2026-09-30",
    )
    assert row["status"] == "usable"
    assert row["fields"]["base_expense_ratio"]["status"] == "unavailable"
    row = absl.derive_one(
        IDENTITY, [summary(suffix="Irregular Growth")], details(), as_of="2026-09-30"
    )
    assert row["fields"]["base_expense_ratio"]["status"] == "unavailable"


def test_direct_retail_and_institutional_never_match_regular():
    for variant, gap in (
        ("Direct Growth", "issuer_direct_plan_conflict"),
        ("Retail Growth", "issuer_non_regular_plan_conflict"),
        ("Institutional Growth", "issuer_non_regular_plan_conflict"),
    ):
        assert absl.bridge(IDENTITY, [summary(suffix=variant)])[1] == gap


def test_no_name_only_or_substring_or_code_only_isin_bridge():
    assert absl.bridge(IDENTITY, [summary(code=100002)])[1] == "issuer_code_isin_bridge_missing"
    assert (
        absl.bridge(IDENTITY, [summary(isin="INF209K01XX0")])[1]
        == "issuer_code_isin_bridge_missing"
    )
    row = absl.derive_one(
        IDENTITY, [summary()], details(DETAIL.replace(NAME, NAME + " II")), as_of="2026-09-30"
    )
    assert row["status"] == "gap"


def test_only_explicit_same_page_erstwhile_name_links_a_renamed_heading():
    old = "Aditya Birla Sun Life Prior Synthetic Fund"
    source = DETAIL.replace(NAME, NAME + "\n(Erstwhile " + old + ")")
    row = absl.derive_one(IDENTITY, [summary(name=old)], details(source), as_of="2026-09-30")
    assert row["status"] == "usable" and row["name_evidence"]["erstwhile"] == [old]
    row = absl.derive_one(IDENTITY, [summary(name=old)], details(), as_of="2026-09-30")
    assert row["status"] == "gap"


def test_missing_unit_or_changed_date_never_becomes_aum():
    for detail in (
        DETAIL.replace("12,345.67 Cr", "12,345.67"),
        DETAIL.replace("AUM as on September 30", "AUM as on September 29"),
    ):
        row = absl.derive_one(IDENTITY, [summary()], details(detail), as_of="2026-09-30")
        assert row["fields"]["aum"]["status"] == "unavailable"
    row = absl.derive_one(IDENTITY, [summary()], details(), as_of="2026-08-31")
    assert row["status"] == "gap" and row["reason"] == "monthly_layout_or_date_differs"


def test_wrong_neighbor_or_direct_change_cannot_supply_current_risk_or_ber():
    row = absl.derive_one(
        IDENTITY,
        [summary()],
        details(changes=CHANGES.replace(NAME, NAME + " II")),
        as_of="2026-09-30",
    )
    assert row["fields"]["base_expense_ratio"]["status"] == "unavailable"
    assert row["fields"]["risk_level"]["status"] == "unavailable"
    row = absl.derive_one(
        IDENTITY,
        [summary()],
        details(changes=CHANGES.replace("Regular 1.24% 1.25%", "Regular 1.24% 1.26%")),
        as_of="2026-09-30",
    )
    assert row["fields"]["base_expense_ratio"]["status"] == "unavailable"


def test_ambiguous_summary_or_monthly_heading_is_a_visible_gap():
    other = summary(name=NAME + " II")
    assert absl.bridge(IDENTITY, [summary(), other])[1] == "issuer_code_isin_bridge_ambiguous"
    row = absl.derive_one(IDENTITY, [summary()], details() * 2, as_of="2026-09-30")
    assert row["status"] == "gap"


def test_bad_unrelated_isin_does_not_erase_a_separately_paired_valid_variant():
    text = f"""SCHEME SUMMARY DOCUMENT
1 Fund Name {NAME}
28 ISINs
{NAME} - Regular Growth {ISIN}
{NAME} - Old Closed Plan
29 AMFI Codes (To be phased out)
{NAME} - Regular Growth 100001
{NAME} - Old Closed Plan 100002
30 SEBI Codes fixture
"""
    rows = absl.summary_pages({"source_hash": "a" * 64, "pages": [page(text, 1)]})
    assert len(rows) == 1 and rows[0]["code"] == 100001
    rows = absl.summary_pages(
        {"source_hash": "a" * 64, "pages": [page(text.replace(ISIN, "ZZ0000000000"), 1)]}
    )
    assert rows == []


def test_conflicting_label_pairs_and_policy_mutation_never_silently_deduplicate():
    label = NAME + " - Regular Growth"
    text = f"1 Fund Name {NAME}\n28 ISINs\n{label} {ISIN}\n{label} {ISIN}\n29 AMFI Codes (To be phased out)\n{label} 100001\n30 SEBI Codes fixture\n"
    assert absl.summary_pages({"source_hash": "a" * 64, "pages": [page(text, 1)]}) == []
    changed = deepcopy(absl.policy())
    changed["fields"].clear()
    assert len(absl.policy()["fields"]) == 5
    assert absl.normalized_name("Large & Mid Cap Fund") == absl.normalized_name(
        "Large and Midcap Fund"
    )
    assert absl.normalized_name("Nifty 50 Fund") != absl.normalized_name("Nifty 500 Fund")
