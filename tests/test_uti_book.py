"""Synthetic monthly layout proofs, not issuer/DEV coverage evidence."""

from datetime import datetime, timezone
import hashlib

import pytest

from mfdataindia.enrichment import uti_book as book


def test_uti_policy_extends_revised_kotak_without_reusing_registered_versions():
    from mfdataindia.enrichment import book_sources
    from mfdataindia.review.policy import fingerprint

    document = book_sources.current_policy(4)
    assert document["version"] == 8 and document["source_registry_version"] == 4
    assert document["previous_policy_hash"] == (
        "4e9edd71a73c781603653164de24227b73f09fa2c369d8cf8b7ad68dd59c6fe3"
    )
    assert fingerprint(document) == (
        "2b1d478498a72a267ecd4719300a5456db52fa0764b2a446e9ee75b545839e5a"
    )
    documents = book_sources.current_policies()
    assert [item["version"] for item in documents] == [4, 5, 6, 7, 8]
    assert len({(item["id"], item["version"]) for item in documents}) == len(documents)


def extraction(
    *,
    as_of="30th September, 2026",
    aum_date=None,
    ter="1.68",
    cover_name="UTI Flexi Cap Fund",
    cover_date="September 30, 2026",
):
    lines = [
        "UTI FLEXI CAP FUND",
        "An open ended dynamic equity scheme",
        "Investment Objective",
        "Benchmark Index",
        "Nifty 500 TRI",
        "Fund Manager",
        "Mr. Ajay Tyagi - Masters in Finance",
        "Managing the scheme since Jan 2016.",
        "Mr. Akash Shah-Assistant Fund Manager - B.Com, CA",
        "Mr. Kamal Gada - Co-Fund Manager, B.Com",
        "Plans/Option (Regular/Direct)",
        "Fund AUM/Folio as on " + (aum_date or as_of),
        "Fund Size Monthly Average : ` Crore23,302.11",
        "Closing AUM : ` Crore22,495.18",
        "Month-end Total Expense Ratio (%)*",
        "Regular : " + ter,
        "Direct : 1.16",
        "Minimum Investment Amount",
    ]
    right = {2: "Portfolio as on " + as_of, 3: "Equity % to NAV", 4: "Wrong Portfolio Ltd. 10.00"}
    text = "\n".join(line.ljust(100) + right.get(n, "") for n, line in enumerate(lines))
    cover = (
        cover_name
        + "\nRisk-o-meter for the fund is based on the portfolio ending "
        + cover_date
        + ".\nThe risk of the scheme is very high"
    )
    return {
        "source_hash": "a" * 64,
        "pages": [
            {"page": 1, "text": cover, "text_hash": hashlib.sha256(cover.encode()).hexdigest()},
            {
                "page": 21,
                "text": text,
                "text_hash": hashlib.sha256(text.encode()).hexdigest(),
                "physical_left_text": "\n".join(lines),
                "physical_left_hash": hashlib.sha256("\n".join(lines).encode()).hexdigest(),
            },
        ],
    }


def fields(value=None):
    value = value or extraction()
    detail = book.detail_pages(value)
    assert len(detail) == 1
    detail[0]["source_url"] = "https://d3ce1o48hc5oli.cloudfront.net/fixture-NOT-ISSUER.pdf"
    return book.fields(detail[0], value, "2026-09-30")


def test_independent_basis_units_complete_managers_and_source_only_TER():
    core, ter, issues = fields()
    assert core["aum"]["value"] == "22495.18" and core["aum"]["unit"] == "INR_crore"
    assert core["aum"]["field_effective_date"] == "2026-09-30"
    assert core["benchmark_name"]["value"] == "Nifty 500 TRI"
    assert core["fund_managers"]["value"] == ["Mr. Ajay Tyagi", "Mr. Akash Shah", "Mr. Kamal Gada"]
    assert core["base_expense_ratio"]["value"] is None
    assert core["base_expense_ratio"]["reason"] == "source_reports_TER_not_BER"
    assert (
        ter["value"] == "1.68" and ter["expense_basis"] == "TER" and ter["scope"] == "regular_plan"
    )
    assert (
        core["risk_level"]["value"] == "Very High"
        and core["risk_level"]["raw_value"] == "very high"
    )
    assert core["risk_level"]["detail_page"] == 1 and not issues


def test_older_printed_AUM_date_is_preserved_not_restamped():
    core, ter, issues = fields(extraction(aum_date="31st July, 2026"))
    assert core["aum"]["value"] is None
    assert issues[0]["explicit_field_dates"] == ["2026-07-31"]
    assert issues[0]["raw_value"] == "22,495.18"
    assert issues[0]["action"] == "withheld_not_restamped_or_repaired"
    assert ter["value"] == "1.68"


@pytest.mark.parametrize(
    "name,date",
    [("UTI Large and Mid Cap Fund", "September 30, 2026"), ("UTI Flexi Cap Fund", "July 31, 2026")],
)
def test_no_other_fund_cover_or_old_risk_date_borrowing(name, date):
    core, _, _ = fields(extraction(cover_name=name, cover_date=date))
    assert core["risk_level"]["value"] is None


def test_actual_reporting_month_is_not_edition_or_AUM_date():
    value = extraction(aum_date="31st July, 2026")
    assert book.period([value], datetime(2026, 10, 17, tzinfo=timezone.utc)) == "2026-09-30"
    value["pages"][1]["text"] += "\nPortfolio as on 31st August, 2026"
    with pytest.raises(ValueError, match="shared"):
        book.period([value], datetime(2026, 10, 17, tzinfo=timezone.utc))


def test_incomplete_or_former_manager_block_is_not_partial_publication():
    value = extraction()
    value["pages"][1]["physical_left_text"] = value["pages"][1]["physical_left_text"].replace(
        "Mr. Kamal Gada -", "Mr. Kamal\nGada -"
    )
    assert fields(value)[0]["fund_managers"]["value"] is None
    value = extraction()
    value["pages"][1]["physical_left_text"] = value["pages"][1]["physical_left_text"].replace(
        "Managing the scheme since", "Managing the scheme until"
    )
    assert fields(value)[0]["fund_managers"]["value"] is None


def test_cost_is_never_direct_TER_or_adjacent_unlabelled_value():
    value = extraction()
    value["pages"][1]["physical_left_text"] = value["pages"][1]["physical_left_text"].replace(
        "Regular : 1.68", "Direct : 1.68"
    )
    assert fields(value)[1]["value"] is None
    assert fields(value)[0]["base_expense_ratio"]["value"] is None


def test_contaminated_benchmark_is_a_gap():
    value = extraction()
    value["pages"][1]["physical_left_text"] = value["pages"][1]["physical_left_text"].replace(
        "Nifty 500 TRI", "Nifty 500 Wrong Portfolio Ltd. TRI"
    )
    assert fields(value)[0]["benchmark_name"]["value"] is None


def test_complete_official_benchmark_name_can_change_without_becoming_price_data():
    value = extraction()
    value["pages"][1]["physical_left_text"] = value["pages"][1]["physical_left_text"].replace(
        "Nifty 500 TRI", "CRISIL Liquid Debt A-I Index"
    )
    assert fields(value)[0]["benchmark_name"]["value"] == "CRISIL Liquid Debt A-I Index"
    assert book.policy()["benchmark_price_series_provided"] is False


def test_former_name_is_only_an_explicit_source_heading_not_a_database_guess():
    value = extraction()
    value["pages"][1]["text"] = value["pages"][1]["text"].replace(
        "UTI FLEXI CAP FUND", "UTI FLEXI CAP FUND (Erstwhile UTI Equity Fund)"
    )
    detail = book.detail_pages(value)[0]
    assert detail["names"] == {"uti flexicap fund", "uti equity fund"}
    assert "unrelated fund" not in detail["names"]


def test_no_coordinate_column_means_no_layout_text_fallback():
    value = extraction()
    value["pages"][1]["physical_left_text"] = None
    assert book.detail_pages(value) == []
