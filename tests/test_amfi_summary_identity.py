"""Synthetic parser/security cases; not issuer coverage or DEV evidence."""

import pytest
from defusedxml.common import DefusedXmlException

from mfdataindia.enrichment import amfi_summary_identity as engine

URL = "https://portal.amfiindia.com/spages/SSD_4372.xml"
CODE = "Direct Plan-Growth-120166 Direct Plan-IDCW-120165 Regular Plan-Growth-112090 Regular Plan-IDCW-120166"
ISINS = "Direct Plan-Growth-INF174K01LS2, Direct Plan-IDCW Payout-INF174K01LQ6, Regular Plan-Growth-INF174K01336 Regular Plan-IDCW Payout-INF174K01351, Regular Plan-IDCW Re-investment-INF174K01344"


def xml(codes=CODE, isins=ISINS, extra=""):
    return (
        "<SchemeSummaryDocument><SchemeSummary><Fund_Name>Kotak Flexicap Fund</Fund_Name>"
        "<AMFI_Codes_To_be_phased_out>" + codes + "</AMFI_Codes_To_be_phased_out>"
        "<ISINs>" + isins + "</ISINs>" + extra + "</SchemeSummary></SchemeSummaryDocument>"
    ).encode()


def identity(code=112090, isin="INF174K01336", option="GROWTH"):
    return {"code": code, "isins": [isin], "plan": "REGULAR", "option": option}


def test_regular_growth_requires_its_explicit_paired_code_and_isin():
    bridges = engine.rows(xml(), URL, "a" * 64)
    matches, reason = engine.exact_matches(identity(), bridges)
    assert reason is None and len(matches) == 1
    assert matches[0]["isin"] == "INF174K01336"
    assert matches[0]["locator"].startswith("/SchemeSummaryDocument/SchemeSummary/")
    assert "page" not in matches[0]
    assert (
        engine.exact_matches(identity(isin="INF174K01351"), bridges)[1]
        == "issuer_code_isin_bridge_missing"
    )


def test_cross_plan_duplicate_code_is_retained_and_rejected_not_repaired():
    bridges = engine.rows(xml(), URL, "a" * 64)
    matches, reason = engine.exact_matches(identity(120166, "INF174K01351", "IDCW"), bridges)
    assert not matches and reason == "issuer_variant_identity_conflict"
    assert {
        tuple(c["variant"]) for r in bridges if r["code"] == 120166 for c in r["code_claims"]
    } == {("DIRECT", "GROWTH"), ("REGULAR", "IDCW")}
    assert (
        engine.exact_matches(identity(112089, "INF174K01351", "IDCW"), bridges)[1]
        == "issuer_code_isin_bridge_missing"
    )


def test_code_first_semicolon_layout_pairs_labelled_isins():
    content = xml(
        "100669-Regular Plan-Growth Option;100668-Regular Plan-IDCW",
        "Regular Plan-Growth-INF789F01513, Regular Plan-Reinvestment of IDCW-INF789F01505, Regular Plan-Payout of IDCW-INF189A01053,",
    )
    bridges = engine.rows(content, URL, "b" * 64)
    assert engine.exact_matches(identity(100669, "INF789F01513"), bridges)[1] is None
    assert engine.exact_matches(identity(100668, "INF789F01505", "IDCW"), bridges)[1] is None


def test_repeated_exact_source_fund_name_optional_plan_and_space_delimiters():
    value = xml(
        "Kotak Flexicap Fund - Regular Growth 112090; Kotak Flexicap Fund - Direct Plan-G 120166",
        "Kotak Flexicap Fund - Regular Growth INF174K01336, Kotak Flexicap Fund - Direct Plan-G INF174K01LS2",
    )
    matches, reason = engine.exact_matches(identity(), engine.rows(value, URL, "a" * 64))
    assert reason is None and matches[0]["isin"] == "INF174K01336"
    assert not engine.rows(
        value.replace(b"Kotak Flexicap Fund - Regular", b"Other Fund - Regular"), URL, "a" * 64
    )


def test_liquid_daily_weekly_and_default_distributions_are_not_cross_joined():
    value = xml(
        "Regular Plan-Daily IDCW-100833 Regular Plan-Weekly IDCW-153586 Regular Plan-IDCW Payout-154605",
        "Regular Plan-Daily IDCW Re-investment-INF174K01NL3 Regular Plan-Weekly IDCW Re-investment-INF174KA1WM1 Regular Plan-Weekly IDCW Payout-INF174KA1WK5 Regular Plan-IDCW Payout-INF174KA1D91",
    )
    bridges = engine.rows(value, URL, "a" * 64)
    assert {(r["code"], r["isin"]) for r in bridges} == {
        (100833, "INF174K01NL3"),
        (153586, "INF174KA1WM1"),
        (153586, "INF174KA1WK5"),
        (154605, "INF174KA1D91"),
    }
    assert (
        engine.exact_matches(identity(100833, "INF174KA1WK5", "IDCW"), bridges)[1]
        == "issuer_code_isin_bridge_missing"
    )


def test_missing_frequency_can_only_pair_a_unique_code_and_unique_isin():
    value = xml("Regular Plan-Daily IDCW-148979", "Regular Plan-IDCW Payout-INF174KA1GY9")
    bridges = engine.rows(value, URL, "a" * 64)
    assert len(bridges) == 1 and bridges[0]["pairing_basis"] == "unique_labeled_plan_option_pair"
    ambiguous = xml(
        "Regular Plan-Daily IDCW-148979 Regular Plan-Weekly IDCW-153586",
        "Regular Plan-IDCW Payout-INF174KA1GY9",
    )
    assert not any(r["isin"] for r in engine.rows(ambiguous, URL, "a" * 64))


def test_unpaired_direct_claim_still_prevents_regular_duplicate_code_acceptance():
    value = xml(
        "Regular Plan-Growth-112090 Direct Plan-G-112090", "Regular Plan-Growth-INF174K01336"
    )
    matches, reason = engine.exact_matches(identity(), engine.rows(value, URL, "a" * 64))
    assert not matches and reason == "issuer_variant_identity_conflict"


def test_explicit_payout_does_not_pair_reinvestment_or_conflicting_frequency_claims():
    assert not any(
        r["isin"]
        for r in engine.rows(
            xml("Regular Plan-IDCW Payout-100833", "Regular Plan-IDCW Reinvestment-INF174K01NL3"),
            URL,
            "a" * 64,
        )
    )
    value = xml(
        "Regular Plan-Daily IDCW-100833 Regular Plan-Weekly IDCW-100833",
        "Regular Plan-Daily IDCW Reinvestment-INF174K01NL3",
    )
    matches, reason = engine.exact_matches(
        identity(100833, "INF174K01NL3", "IDCW"), engine.rows(value, URL, "a" * 64)
    )
    assert not matches and reason == "issuer_variant_identity_conflict"


@pytest.mark.parametrize(
    "codes,isins",
    [
        (CODE + " unparsed 999999", ISINS),
        (CODE, ISINS + " unpaired identifier"),
        ("Regular Plan-Growth-112090", "Regular Plan-Growth-INF174K01337"),
        ("112090 - Growth", ISINS),
        ("112090-Regular Plan-Growth;unknown", ISINS),
    ],
)
def test_partial_invalid_or_unlabelled_tokens_cannot_create_a_bridge(codes, isins):
    assert not any(r["isin"] for r in engine.rows(xml(codes, isins), URL, "a" * 64))


def test_unpaired_claim_in_another_family_also_blocks_a_contradictory_code():
    paired = engine.rows(
        xml("Regular Plan-Growth-112090", "Regular Plan-Growth-INF174K01336"), URL, "a" * 64
    )
    content = xml("Direct Plan-G-112090", "Direct Plan-G-INF174K01LS2").replace(
        b"Kotak Flexicap Fund", b"Other Issuer Fund"
    )
    other = engine.rows(content, "https://portal.amfiindia.com/spages/SSD_99.xml", "b" * 64)
    assert other and all(r["isin"] is None for r in other)
    assert engine.exact_matches(identity(), paired + other)[1] == "issuer_variant_identity_conflict"


def test_undated_financial_values_never_become_monthly_observations():
    value = engine.rows(
        xml(
            extra="<Riskometer_as_on_Date>Low</Riskometer_as_on_Date><Annual_Expense_Stated_maximum>0</Annual_Expense_Stated_maximum>"
        ),
        URL,
        "a" * 64,
    )
    assert value
    assert all("risk" not in key and "expense" not in key for row in value for key in row)


@pytest.mark.parametrize(
    "prefix",
    [
        '<!DOCTYPE root [<!ENTITY x "bad">]>',
        '<!DOCTYPE root SYSTEM "https://attacker.invalid/a">',
    ],
)
def test_no_dtd_entities_or_external_references(prefix):
    with pytest.raises(DefusedXmlException):
        engine.rows(prefix.encode() + xml(), URL, "a" * 64)


def test_duplicate_or_other_summary_shapes_remain_gaps():
    assert not engine.rows(xml(extra="<Fund_Name>Other</Fund_Name>"), URL, "a" * 64)
    assert not engine.rows(b"<root><Fund_Name>Other</Fund_Name></root>", URL, "a" * 64)


@pytest.mark.parametrize(
    "label",
    [
        "Direct Plan - Growth",
        "Regular and Direct Plan - Growth",
        "Regular Plan - Growth IDCW",
        "Retail Plan - Growth",
        "Regular Plan - Bonus",
        "Regular Plan - Daily Growth",
        "Regular Plan - Growth Payout",
    ],
)
def test_only_unambiguous_regular_current_options_qualify(label):
    bridges = engine.rows(xml("112090-" + label, ISINS), URL, "a" * 64)
    assert engine.exact_matches(identity(), bridges)[1] is not None
