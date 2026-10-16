"""Synthetic layout cases; real original-source acceptance is a separate gate."""

from copy import deepcopy

import pytest

from mfdataindia.enrichment import monthly_facts as engine, monthly_pdf, pdf_evidence
from test_official_facts import DETAIL, SUMMARY, LINEAGE, identity

CHANGES = """Aditya Birla Sun Life Large Cap Fund
August 2026
Changes in Risk-o-Meter of scheme
Change in Previous Month Current Month
Scheme Riskometer High Very High
Changes in Expense Ratio of the scheme
Change in Previous Month Current Month
Regular 1.30% 1.35%
"""


def parsed(**kwargs):
    args = {
        "identity": identity(code=103174),
        "detail": DETAIL,
        "changes": CHANGES,
        "registry": SUMMARY.replace("-Growth 1\n", "-Growth 103174\n").replace(
            "-Regular - IDCW 2\n", "-Regular - IDCW 103173\n"
        ),
        "as_of": "2026-08-31",
        "detail_lineage": LINEAGE,
        "changes_lineage": {**LINEAGE, "page": 16},
        "identity_lineage": {**LINEAGE, "page": 28},
    }
    return engine.parse(**{**args, **kwargs})


def test_current_scheme_risk_not_previous_legend_or_benchmark_and_no_human_verification():
    value = parsed()
    assert value == parsed()
    assert value["fields"]["risk_level"]["value"] == "Very High"
    assert "Current Month" in value["fields"]["risk_level"]["source_anchor"]
    assert value["verification_status"] == "needs_verification"
    assert not value["canonical_analytics_replaced"]
    assert value["fields"]["total_expense_ratio"]["value"] is None
    assert value["fields"]["aum"]["reason"] == "unit_not_explicit"


@pytest.mark.parametrize(
    "changes",
    [
        CHANGES.replace("Scheme Riskometer High Very High", "Benchmark Riskometer High Very High"),
        CHANGES.replace(
            "Scheme Riskometer High Very High", "Riskometer Low Moderate High Very High"
        ),
        CHANGES + "Scheme Riskometer High Low\n",
        CHANGES.replace("Changes in Risk-o-Meter of scheme", "Changes in Benchmark Risk"),
    ],
)
def test_missing_ambiguous_or_wrong_risk_anchor_is_explicitly_unavailable(changes):
    result = parsed(changes=changes)["fields"]["risk_level"]
    assert result["status"] == "unavailable" and result["value"] is None


def test_explicit_aum_unit_date_and_original_token_required():
    result = parsed(detail=DETAIL.replace("29,710.82\n", "29,710.82 Cr\n"))
    field = result["fields"]["aum"]
    assert field["value"] == "29710.82" and field["unit"] == "INR_crore"
    assert field["raw_value"] == "29,710.82 Cr"
    assert field["field_effective_date"] == "2026-08-31"
    other_date = DETAIL.replace("AUM as on August 31", "AUM as on July 31").replace(
        "29,710.82\n", "29,710.82 Cr\n"
    )
    assert parsed(detail=other_date)["fields"]["aum"]["reason"] == "field_date_differs"


def test_disagreeing_current_month_ber_never_enters_accepted_current_values():
    value = parsed(changes=CHANGES.replace("1.30% 1.35%", "1.30% 1.36%"))
    assert value["fields"]["base_expense_ratio"]["value"] is None
    assert (
        value["fields"]["base_expense_ratio"]["reason"] == "detail_and_current_month_BER_disagree"
    )


@pytest.mark.parametrize(
    "update",
    [
        {"identity": identity(code=99)},
        {"identity": identity(code=103174, plan="DIRECT")},
        {"identity": identity(code=103174, scheme_name="Other Large Cap Fund")},
        {"changes": CHANGES.replace("August 2026", "September 2026")},
        {"changes": CHANGES.replace(engine.NAME, "Other Fund")},
        {"as_of": "2026-09-30"},
    ],
)
def test_exact_identity_and_two_page_date_required(update):
    with pytest.raises(ValueError):
        parsed(**update)


def test_monthly_pdf_policy_additive_and_limits_fail_before_worker():
    assert monthly_pdf.policy()["maximum_source_bytes"] == 16 * 1024 * 1024
    assert pdf_evidence.policy()["maximum_source_bytes"] == 8 * 1024 * 1024
    p = deepcopy(monthly_pdf.policy())
    p["maximum_pages"] = 1
    assert monthly_pdf.policy()["maximum_pages"] == 400
    for start, end in ((0, 1), (1, 25), (4, 3), (True, 2), (399, 401)):
        with pytest.raises(ValueError):
            monthly_pdf.extract(b"%PDF-synthetic", start=start, end=end)


def test_no_currency_percent_or_locale_guessing():
    assert engine.english_date("January 07,2026") == "2026-01-07"
    assert engine.decimal("27,932.68") == "27932.68"
    for token in ("1,23.4", "NaN", "Infinity", "1e3", "₹ 123", "1.2%"):
        with pytest.raises(ValueError):
            engine.decimal(token)
    with pytest.raises(ValueError):
        engine.english_date("Janvier 07,2026")
