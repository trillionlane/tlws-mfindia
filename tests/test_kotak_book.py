"""Synthetic layout/unit/date cases, not proof of issuer or DEV coverage."""

from datetime import datetime, timezone
import hashlib

import pytest

from mfdataindia.enrichment import kotak_book as book


def text(managers=None):
    managers = managers or ["Mr. Harsha Upadhyaya"]
    left = [
        "KOTAK FLEXI CAP FUND",
        "(ERSTWHILE KNOWN AS KOTAK FLEXICAP FUND)",
        "An open-ended dynamic equity scheme",
        "Fund Manager*: " + managers[0],
        *managers[1:],
        "AAUM: `54,593.64 crs",
        "AUM: `53,086.64 crs",
        "Benchmark***: Nifty 500 TRI (Tier 1)",
        "             Nifty 200 TRI (Tier 2)",
        "Allotment Date: September 11, 2009",
        "Folio Count data as on 31st August 2026.",
        "Net Asset Value (NAV)",
        "(as on September 30, 2026)",
        "Expense Ratio**",
        "Regular Plan: 1.44%",
        "Direct Plan: 0.61%",
        "Data as on 30th September, 2026 unless",
        "otherwise specified.",
    ]
    right = [
        "",
        "",
        "",
        "PORTFOLIO",
        "Issuer/Instrument",
        "Holding AUM 99.00",
        "Other benchmark PRI",
    ]
    result = "\n".join(
        line.ljust(80) + (right[n] if n < len(right) else "") for n, line in enumerate(left)
    )
    return (
        result
        + "\nThe risk of the scheme is Very High\nThe risk of the benchmark is Low\n**The expense ratio disclosed above represents the Base Expense Ratio (BER), excluding other costs."
    )


def extraction(value=None):
    value = value or text()
    return {
        "source_hash": "a" * 64,
        "pages": [
            {"page": 9, "text": value, "text_hash": hashlib.sha256(value.encode()).hexdigest()}
        ],
    }


def fields(value=None, as_of="2026-09-30"):
    pages = book.detail_pages(extraction(value))
    assert len(pages) == 1
    return book.fields(pages[0], as_of)


def test_five_labelled_values_not_adjacent_aaum_direct_or_secondary_benchmark():
    result = fields()
    assert {k: v["value"] for k, v in result.items()} == {
        "benchmark_name": "Nifty 500 TRI (Tier 1)",
        "fund_managers": ["Mr. Harsha Upadhyaya"],
        "base_expense_ratio": "1.44",
        "aum": "53086.64",
        "risk_level": "Very High",
    }
    assert result["aum"]["field_effective_date"] == "2026-09-30"
    assert result["benchmark_name"]["field_effective_date"] is None
    assert result["aum"]["raw_value"] == "53,086.64"


@pytest.mark.parametrize(
    "old,new,key",
    [
        ("AUM: `53,086.64 crs", "Other: `53,086.64 crs", "aum"),
        ("`53,086.64 crs", "`53,086.64", "aum"),
        ("Base Expense Ratio (BER)", "Total Expense Ratio (TER)", "base_expense_ratio"),
        ("Regular Plan: 1.44%", "Direct Plan: 1.44%", "base_expense_ratio"),
        ("Benchmark***:", "Comparison***:", "benchmark_name"),
        ("Nifty 500 TRI (Tier 1)", "Nifty 500 TRI (Tier 2)", "benchmark_name"),
        ("The risk of the scheme is", "The risk of the benchmark is", "risk_level"),
        ("Mr. Harsha Upadhyaya", "Mr. Harsha Upadhyaya and unknown manager", "fund_managers"),
    ],
)
def test_specific_missing_or_contradictory_anchors_never_borrow_another_value(old, new, key):
    value = text().replace(old, new)
    # Keep a recognisable page when testing missing AUM semantics.
    if key == "aum" and new.startswith("Other"):
        value = value.replace("Other: `53,086.64 crs", "AUM: unavailable")
    assert fields(value)[key]["value"] is None


def test_url_or_edition_month_does_not_stamp_a_stale_document():
    value = text().replace("30th September, 2026", "31st July, 2026")
    actual = book.period(extraction(value), datetime(2026, 10, 17, tzinfo=timezone.utc))
    assert actual == "2026-07-31"
    assert all(v["value"] is None for v in fields(value).values())
    assert fields(value, actual)["aum"]["field_effective_date"] == "2026-07-31"


def test_nav_and_folio_dates_are_not_a_fund_reporting_context():
    value = text().replace("Data as on 30th September, 2026 unless", "No fund-level context")
    assert all(v["value"] is None for v in fields(value).values())
    with pytest.raises(ValueError):
        book.period(extraction(value), datetime(2026, 10, 17, tzinfo=timezone.utc))


def test_conflicting_actual_reporting_dates_are_not_resolved_using_the_filename():
    value = text() + "\nData as on 31st August, 2026\n"
    with pytest.raises(ValueError):
        book.period(extraction(value), datetime(2026, 10, 17, tzinfo=timezone.utc))


def test_month_end_nonfuture_dates_are_required():
    with pytest.raises(ValueError):
        book.period(
            extraction(text().replace("30th September", "29th September")),
            datetime(2026, 10, 17, tzinfo=timezone.utc),
        )
    with pytest.raises(ValueError):
        book.period(extraction(), datetime(2026, 9, 29, tzinfo=timezone.utc))


def test_complete_wrapped_manager_block_retains_all_names():
    value = text(["Mr. First Manager,", "Mr. Second Manager"])
    assert fields(value)["fund_managers"]["value"] == ["Mr. First Manager", "Mr. Second Manager"]


def test_duplicate_scheme_risk_is_ambiguous_even_if_other_metrics_exist():
    value = text() + "\nThe risk of the scheme is High\n"
    assert fields(value)["risk_level"]["value"] is None


def test_wrapped_primary_benchmark_is_complete_and_parallel_panels_fail_closed():
    value = (
        text()
        .replace("Other benchmark PRI", "")
        .replace("Nifty 500 TRI (Tier 1)", "Nifty Large Midcap\n250 TRI (Tier 1),")
    )
    assert fields(value)["benchmark_name"]["value"] == "Nifty Large Midcap 250 TRI (Tier 1)"
    value = text().replace(
        "Nifty 500 TRI (Tier 1)", "Nifty Short Duration Benchmark: Nifty Short Duration"
    )
    assert fields(value)["benchmark_name"]["value"] is None
    value = text().replace("Nifty 500 TRI (Tier 1)", "Nifty Composite")
    assert fields(value)["benchmark_name"]["value"] is None


def test_global_reporting_context_does_not_require_a_specific_footer_line_wrap():
    value = text().replace("unless", "").replace("otherwise specified.", "")
    assert fields(value)["aum"]["value"] == "53086.64"


@pytest.mark.parametrize(
    "label",
    [
        "Nifty Midcap 50 IndusInd Bank Ltd. TRI",
        "NIFTY India M Consumption Index M TRI",
        "CRISIL Medium Duration Debt A-III Duration Debt A-III Index Index",
        "Nifty SDL Apr 2032 7 Top 12 Equal Weight G Index",
    ],
)
def test_layout_contamination_is_not_published_as_a_benchmark(label):
    assert (
        fields(text().replace("Nifty 500 TRI (Tier 1)", label))["benchmark_name"]["value"] is None
    )


def test_ambiguous_layout_column_is_not_cut_at_a_guessed_position():
    assert (
        book.detail_pages(extraction(text().replace("Issuer/Instrument", "Unrecognised layout")))
        == []
    )


def test_explicit_former_name_is_source_authored_not_a_database_name_repair():
    detail = book.detail_pages(extraction())[0]
    assert book.normalize("Kotak Flexicap Fund") in detail["names"]
    assert book.normalize("Other Fund") not in detail["names"]
