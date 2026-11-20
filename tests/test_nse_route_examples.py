"""Public identity subset plus synthetic limits; never production publication authority."""

from copy import deepcopy
from datetime import date
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from mfdataindia.api.schemas import NseSourceSection
from mfdataindia.nse import contract as c, sch44_reconcile as review, source_native as native
from mfdataindia.nse.schema import HEADERS44
from test_nse_sch44_reconcile import SOURCE
from test_nse_source_native import inputs

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/nse01/sch44_production_identity_subset.json").read_text()
)


def sample_source():
    header, sample, *_ = SOURCE.decode().splitlines()
    rows = []
    for row in FIXTURE["rows"]:
        cells = sample.split("|")
        cells[0] = str(row["source_line"])
        for field, value in row["fields"].items():
            cells[HEADERS44.index(field)] = value
        rows.append("|".join(cells))
    return ("\n".join([header, *rows]) + "\n").encode()


def amfi_fixture(day):
    rows = [
        "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date",
        "Open Ended Schemes (Hybrid Scheme - Conservative Hybrid Fund)",
        "Aditya Birla Sun Life Mutual Fund",
    ]
    for fund in FIXTURE["amfi_variants"]:
        rows.append(
            ";".join(
                [
                    str(fund["amfi_scheme_code"]),
                    fund["isin_growth_or_div_payout"] or "-",
                    fund["isin_div_reinvest"] or "-",
                    fund["scheme_name"],
                    "Regular Plan" if fund["plan_type"] == "REGULAR" else "Direct Plan",
                    "Growth" if fund["option_type"] == "GROWTH" else "Monthly IDCW Option",
                    "10",
                    day,
                ]
            )
        )
    return ("\n".join(rows) + "\n").encode()


def test_conservative_hybrid_companions_remain_distinct_and_production_values_literal():
    funds = {f["amfi_scheme_code"]: f for f in FIXTURE["amfi_variants"]}
    assert len(funds) == 5
    for code, isin, plan, option in [
        (101818, "INF209K01751", "REGULAR", "GROWTH"),
        (120705, "INF209K01XH4", "DIRECT", "GROWTH"),
        (101816, "INF209K01769", "REGULAR", "IDCW"),
        (120550, "INF209K01R47", "DIRECT", "IDCW"),
        (154772, "INF209KC1761", "REGULAR", "GROWTH"),
    ]:
        assert (
            funds[code]["isin_growth_or_div_payout"],
            funds[code]["plan_type"],
            funds[code]["option_type"],
        ) == (isin, plan, option)
    routes = {r["fields"]["SCHEME CODE"]: r["fields"] for r in FIXTURE["rows"]}
    assert routes["B313G"]["ISIN"] == routes["B313G-L1"]["ISIN"] == "INF209K01751"
    assert routes["B313G"]["RTA SCHEME CODE"] == routes["B313G-L1"]["RTA SCHEME CODE"] == "313G"
    assert routes["B313G"]["SETTLEMENT TYPE"] == "T1"
    assert routes["B313G-L1"]["SETTLEMENT TYPE"] == "L1"
    assert routes["B141B-GR-L1"]["ISIN"] == "INF209KC1761"
    assert routes["B141B-GR-L1"]["RTA SCHEME CODE"] == "141B"
    assert routes["B141B-GR-L1"]["PLAN TYPE"] == "NORMAL"
    assert all(r["SIP ALLOWED"] == "Y" for r in routes.values())
    # The Direct counterparts are catalogue candidates, not invented source routes.
    assert not {
        f["isin_growth_or_div_payout"] for f in funds.values() if f["plan_type"] == "DIRECT"
    } & {r["ISIN"] for r in routes.values()}


def test_uat_examples_do_not_replace_missing_production_issuer_binding():
    raw = sample_source()
    catalogue = c.canonical(
        {
            "format": "nse01-mfdata-catalogue-v1",
            "as_of": "2026-11-18",
            "dataset_version": "92-identity-fixture",
            "funds": FIXTURE["amfi_variants"],
        }
    )
    earlier = raw.replace(b"REGULAR-GROWTH", b"Retained title only")
    result = review.report(
        raw, catalogue, earlier, amfi_fixture("14-Nov-2026"), amfi_fixture("16-Nov-2026")
    )
    assert len(result["rows"]) == len(FIXTURE["rows"])
    assert all(
        r["status"] in {"UNMATCHED", "AMBIGUOUS"} and r["reason"] == "ISSUER_NOT_CORROBORATED"
        for r in result["rows"]
    )
    assert all(r["amfi_isin_slot"] is None for r in result["rows"])
    assert {r["nse_scheme_code"] for r in result["rows"]} == {
        r["fields"]["SCHEME CODE"] for r in FIXTURE["rows"]
    }


def wire_row(flag, scheme, settlement):
    plan = native.prepare(**inputs())
    item = plan["rows"][0]
    cells = list(item["cells"])
    cells[1] = scheme
    cells[28] = flag
    cells[32] = settlement
    return {
        "source_line": 2,
        "snapshot_id": "00000000-0000-4000-8000-000000000001",
        "source_sha256": "1" * 64,
        "generation_date": date(2026, 11, 17),
        "retrieved_at_utc": "2026-11-17T13:03:30.761Z",
        "filename": "NSE_NSEINVEST_ALL_17112026.txt",
        "catalogue_version": "fixture",
        "candidates": [item["link"]],
        "identity_evidence_hashes": plan["metadata"]["identity_evidence"],
        "identity_rule": plan["metadata"]["identity_rule"],
        "original_cells": cells,
        "field_holds": item["field_holds"],
    }


@pytest.mark.parametrize(
    ("flag", "expected"), [("Y", True), ("N", False), ("", None), ("1", None), ("?", None)]
)
@pytest.mark.parametrize(("scheme", "settlement"), [("CODE-L1", "T1"), ("UNSUFFIXED", "OTHER")])
def test_sip_support_comes_only_from_literal_production_field_not_code_or_uat(
    flag, expected, scheme, settlement
):
    row = wire_row(flag, scheme, settlement)
    conn = Mock()
    conn.execute.side_effect = [
        Mock(fetchone=lambda: {"value": "mf_nse.source_routes"}),
        Mock(fetchall=lambda: [row]),
    ]
    result = native.fund_section(conn, 100001)
    wire = NseSourceSection.model_validate(result).model_dump(mode="json", by_alias=True)
    (route,) = wire["routes"]
    assert route["fields"] == dict(zip(HEADERS44, row["original_cells"]))
    assert len(route["fields"]) == 44
    assert route["source_sip_allowed"] is expected
    assert route["fields"]["SIP ALLOWED"] == flag
    assert route["fields"]["SETTLEMENT TYPE"] == settlement
    assert route["fields"]["SCHEME CODE"] == scheme
    assert wire["transaction_eligibility"] == "NOT_ESTABLISHED"
    evidence = route["identity_evidence"]
    assert evidence["amfi_scheme_code"] == 100001
    assert evidence["historical_route"] == "CORROBORATED"
    assert len(evidence["amfi_witnesses"]) == 2
    assert evidence["amfi_witnesses"][0]["sha256"] == row["identity_evidence_hashes"]["amfi_first"]


def test_current_only_evidence_serializes_no_fabricated_prior_source_line():
    row = wire_row("Y", "NEW_ROUTE-L1", "T1")
    row["candidates"] = deepcopy(row["candidates"])
    row["candidates"][0]["earlier_nse_line"] = None
    conn = Mock()
    conn.execute.side_effect = [
        Mock(fetchone=lambda: {"value": "mf_nse.source_routes"}),
        Mock(fetchall=lambda: [row]),
    ]
    result = NseSourceSection.model_validate(native.fund_section(conn, 100001)).model_dump(
        mode="json", by_alias=True
    )
    evidence = result["routes"][0]["identity_evidence"]
    assert evidence["earlier_nse_line"] is None
    assert evidence["historical_route"] == "ABSENT_NO_ADVERSE_HISTORY"
