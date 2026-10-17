"""Synthetic parser/security proofs, not issuer or DEV certification evidence."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

import pytest
from defusedxml.common import DefusedXmlException

from mfdataindia.enrichment import sbi_acquisition as acquisition, sbi_book as sbi

ACTIVE = """15
Regular Plan - Growth 104.9731
Regular Plan - IDCW 47.0046
AUM as on 30/09/2026: ` 22,109.87 Cr
AAUM as on 30/09/2026: ` 22,652.19 Cr
Name of Fund
Managers
Total
Experience
Managing
Since
Mr. Anup
Upadhyay 18 years Dec-2024
TER BER
Regular 2.04% 1.40%
Direct 1.25% 0.72%
Date of Inception / :
Allotment
Benchmark Index :
NAV :
29/09/2005
BSE 500 (TRI)
Lump sum Amount: `1,000/-
The risk of the scheme is Very High
The risk of the benchmark is Low
Type of Scheme: An open-ended Dynamic Equity Scheme
SBI FLEXICAP FUND
Investment Objective: Example synthetic body.
Fund Details
"""
XML = b"""<root><Fund_Name>SBI Flexicap Fund</Fund_Name><Options_Names>
<item><AMFICode>103215</AMFICode><ISIN>INF200K01222</ISIN><OptionsNames>Regular Plan - Growth</OptionsNames></item>
<item><AMFICode>103216</AMFICode><ISIN>INF200K01248</ISIN><OptionsNames>Regular Plan - IDCW</OptionsNames></item>
</Options_Names><Riskometer_as_on_Date>Low</Riskometer_as_on_Date></root>"""
IDENTITY = {
    "code": 103215,
    "amc": sbi.AMC,
    "plan": "REGULAR",
    "option": "GROWTH",
    "scheme_name": "SBI Flexicap Fund - Regular Plan - Growth",
    "isins": ["INF200K01222"],
}


def extraction(text=ACTIVE):
    return {
        "source_hash": "a" * 64,
        "pages": [
            {"page": 18, "text": text, "text_hash": hashlib.sha256(text.encode()).hexdigest()}
        ],
    }


def bridges(raw=XML):
    return sbi.identity_rows(raw, "https://portal.amfiindia.com/spages/SSD_11605.xml", "b" * 64)


def test_labelled_values_not_neighbouring_ter_aum_or_benchmark_risk():
    result = sbi.fields(ACTIVE, "2026-09-30")
    assert {k: v["value"] for k, v in result.items()} == {
        "benchmark_name": "BSE 500 (TRI)",
        "fund_managers": ["Mr. Anup Upadhyay"],
        "base_expense_ratio": "1.40",
        "aum": "22109.87",
        "risk_level": "Very High",
    }
    assert result["aum"]["field_effective_date"] == "2026-09-30"
    assert result["risk_level"]["field_effective_date"] is None
    assert result["base_expense_ratio"]["raw_value"] == "1.40"
    assert sbi.fields(ACTIVE, "2026-08-31")["aum"]["status"] == "unavailable"


@pytest.mark.parametrize(
    "old,new,field",
    [
        ("TER BER", "TER only", "base_expense_ratio"),
        ("Regular 2.04% 1.40%", "Direct 2.04% 1.40%", "base_expense_ratio"),
        ("AUM as on 30/09/2026:", "AAUM as on 30/09/2026:", "aum"),
        ("22,109.87 Cr", "22,109.87", "aum"),
        ("The risk of the scheme is", "The risk of the benchmark is", "risk_level"),
        ("Name of Fund", "Name of adviser", "fund_managers"),
    ],
)
def test_missing_specific_anchor_never_borrows_another_metric(old, new, field):
    assert sbi.fields(ACTIVE.replace(old, new), "2026-09-30")[field]["value"] is None


def test_duplicate_and_partial_manager_blocks_are_not_accepted():
    text = ACTIVE.replace("TER BER", "Mr. Unknown (unparseable)\nTER BER")
    assert sbi.fields(text, "2026-09-30")["fund_managers"]["value"] is None
    text = ACTIVE + "\nThe risk of the scheme is Moderate\n"
    assert sbi.fields(text, "2026-09-30")["risk_level"]["value"] is None


def test_identities_have_real_xml_locators_not_fabricated_pdf_pages():
    rows = bridges()
    assert len(rows) == 2 and rows[0]["code"] == 103215
    assert rows[0]["locator"] == "/root/Options_Names/item[1]" and "page" not in rows[0]
    assert rows[0]["source_hash"] == hashlib.sha256(XML).hexdigest()
    assert "risk_level" not in rows[0]
    result = sbi.derive(IDENTITY, rows, sbi.detail_pages(extraction()), "2026-09-30")
    assert result["status"] == "usable"
    assert all(v["content_hash"] == "a" * 64 for v in result["fields"].values())


@pytest.mark.parametrize(
    "key,value",
    [
        ("amc", "Unrelated Mutual Fund"),
        ("plan", "DIRECT"),
        ("option", "IDCW"),
        ("isins", ["INF200K01248"]),
    ],
)
def test_current_identity_mismatch_cannot_publish(key, value):
    current = {**IDENTITY, key: value}
    assert (
        sbi.derive(current, bridges(), sbi.detail_pages(extraction()), "2026-09-30")["status"]
        == "gap"
    )


def test_conflicting_issuer_variant_rejects_even_if_one_pair_matches():
    rows = bridges()
    rows.append({**rows[0], "label": "Direct Plan - Growth", "isin": "INF200K01UF3"})
    assert (
        sbi.derive(IDENTITY, rows, sbi.detail_pages(extraction()), "2026-09-30")["reason"]
        == "issuer_variant_identity_conflict"
    )


def test_exact_monthly_heading_and_unique_primary_page_are_required():
    pages = sbi.detail_pages(extraction(ACTIVE.replace("SBI FLEXICAP FUND", "SBI OTHER FUND")))
    assert sbi.derive(IDENTITY, bridges(), pages, "2026-09-30")["status"] == "gap"
    pages = sbi.detail_pages(extraction())
    assert sbi.derive(IDENTITY, bridges(), pages + deepcopy(pages), "2026-09-30")["status"] == "gap"
    continuation = ACTIVE.replace("Fund Details", "Additional Fund Details")
    assert sbi.detail_pages(extraction(continuation)) == []


@pytest.mark.parametrize(
    "raw",
    [
        b'<!DOCTYPE root [<!ENTITY x "bad">]><root>&x;</root>',
        b'<!DOCTYPE root SYSTEM "https://attacker.invalid/a"><root/>',
    ],
)
def test_xml_dtd_and_entities_are_forbidden(raw):
    with pytest.raises(DefusedXmlException):
        bridges(raw)


def test_other_xml_layout_is_an_explicit_gap_not_a_guessed_join():
    assert (
        bridges(
            b"<SchemeSummaryDocument><FundName>SBI Flexicap Fund</FundName></SchemeSummaryDocument>"
        )
        == []
    )


def catalogue():
    return "".join(
        '<a href="https://www.sbimf.com/docs/default-source/scheme-factsheets/'
        + kind
        + "-factsheet-"
        + month
        + '-2026.pdf?sfvrsn=ab12_4">x</a>'
        for month in ("september", "august")
        for kind in ("all-sbimf-schemes", "sbi-mf-passives-(index-etf-fof)")
    )


def test_select_two_month_end_periods_and_both_actual_books():
    result = sbi.editions(catalogue().encode(), datetime(2026, 10, 17, tzinfo=timezone.utc))
    assert [r["as_of"] for r in result] == ["2026-09-30", "2026-08-31"]
    assert all(set(r["documents"]) == {"active", "passive"} for r in result)
    with pytest.raises(ValueError, match="complete"):
        sbi.editions(
            catalogue()
            .replace("sbi-mf-passives-(index-etf-fof)-factsheet-september", "unknown")
            .encode(),
            datetime(2026, 10, 17),
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://www.sbimf.com/factsheets",
        "https://127.0.0.1/a",
        sbi.CATALOGUE + "?x=1",
        sbi.DIRECTORY + ",GATE=true",
        "https://www.sbimf.com@attacker.invalid/a",
        "https://validate.perfdrive.com/challenge",
        sbi.DIRECTORY + "\n",
    ],
)
def test_transport_rejects_unregistered_urls_before_network(url):
    with pytest.raises(ValueError):
        acquisition.classify(url)


def test_identity_url_must_be_returned_by_matching_scheme_index():
    doc = {
        "data": [
            {
                "schemeId": "11605",
                "summaryXmlUrl": "https://portal.amfiindia.com/spages/SSD_11605.xml",
            }
        ]
    }
    assert acquisition.identity_url(json.dumps(doc).encode(), "11605").endswith("11605.xml")
    with pytest.raises(ValueError):
        acquisition.identity_url(json.dumps(doc).encode(), "11592")
    doc["data"][0]["summaryXmlUrl"] = "https://attacker.invalid/SSD_11605.xml"
    with pytest.raises(ValueError):
        acquisition.identity_url(json.dumps(doc).encode(), "11605")


READY = """Details provided are as on August 31st, 2026.
*AUM as on August 31st, 2026
Passive Investment Solutions - Ready Reckoner
Scheme Name Inception Date Benchmark AUM (Rs. Crs)* BER/TER BER/TER Tracking Error
Regular plan Direct plan
SBI FLEXICAP FUND 29-09-2005 BSE 500 TRI 13,913 0.34/0.44 0.16/0.24 0.0166% -0.2603%
"""


def test_older_passive_ready_reckoner_is_three_explicit_fields_not_a_fake_primary_page():
    rows = sbi.detail_pages(extraction(READY))
    assert len(rows) == 1 and rows[0]["layout"] == "ready_reckoner_v1"
    values = sbi.ready_fields(rows[0], "2026-08-31")
    assert {k: v["value"] for k, v in values.items()} == {
        "benchmark_name": "BSE 500 TRI",
        "aum": "13913",
        "base_expense_ratio": "0.34",
        "fund_managers": None,
        "risk_level": None,
    }
    assert all(v["value"] is None for v in sbi.ready_fields(rows[0], "2026-09-30").values())
    assert sbi.derive(IDENTITY, bridges(), rows, "2026-08-31")["detail_page"] == 18


@pytest.mark.parametrize(
    "old,new",
    [
        ("BER/TER BER/TER", "TER/BER TER/BER"),
        ("Regular plan Direct plan", "Direct plan"),
        ("AUM (Rs. Crs)*", "AUM (Rs. Lakhs)*"),
        ("*AUM as on", "AAUM as on"),
    ],
)
def test_ready_reckoner_rejects_ambiguous_units_column_order_or_dates(old, new):
    assert sbi.detail_pages(extraction(READY.replace(old, new))) == []


def test_primary_detail_precedes_same_fund_ready_reckoner_but_duplicate_rows_stay_ambiguous():
    primary = extraction()
    other = extraction(READY)["pages"][0]
    primary["pages"].append({**other, "page": 19})
    rows = sbi.detail_pages(primary)
    assert len(rows) == 1 and "layout" not in rows[0]
    assert (
        sbi.derive(IDENTITY, bridges(), rows, "2026-09-30")["fields"]["aum"]["value"] == "22109.87"
    )
    duplicate = extraction(READY)
    duplicate["pages"].append({**duplicate["pages"][0], "page": 19})
    assert (
        sbi.derive(IDENTITY, bridges(), sbi.detail_pages(duplicate), "2026-08-31")["status"]
        == "gap"
    )


def test_incidental_date_in_performance_or_aaum_cannot_date_other_fields():
    text = ACTIVE.replace("AUM as on 30/09/2026:", "AUM as on 31/08/2026:")
    assert all(v["value"] is None for v in sbi.fields(text, "2026-09-30").values())
