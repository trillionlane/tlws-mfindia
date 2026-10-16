"""Bounded HDFC layout, preserving source names, decimal tokens and manager roles."""

import hashlib
import re
from pathlib import Path

from mfdataindia.enrichment import multi_facts, monthly_pdf, idcw_discovery
from mfdataindia.enrichment.monthly_schemas import MonthlyFact
from mfdataindia.review.policy import fingerprint


def one(pattern, text, flags=0):
    values = re.findall(pattern, text, flags)
    if len(values) != 1:
        raise ValueError("missing or ambiguous HDFC source anchor")
    return values[0]


def fields(detail, continuation, as_of):
    text = " ".join(detail.split())
    notes = " ".join(continuation.split())
    label = "September 30, 2026"
    for page in (detail, continuation):
        one(r"(?m)^\s*HDFC Flexi Cap Fund\s*$", page)
        one(r"(?m)^\s*[78]\s*\|\s*September 2026\s*$", page)
    if as_of != "2026-09-30":
        raise ValueError("reviewed HDFC edition date differs")
    one(r"NAV \(As On SEPTEMBER 30, 2026\)", text)
    benchmark = one(r"#BENCHMARK INDEX (.*?) ##ADDL\. BENCHMARK INDEX", text)
    expense = one(r"EXPENSE RATIO \(As On " + label + r"\) (.*?) #BENCHMARK INDEX", text)
    if not expense.startswith(
        "Base expense Ratio Including statutory levies on expenses part of BER, excluding brokerage, transaction cost and execution related statutory levies^"
    ):
        raise ValueError("BER label differs; no TER substitution permitted")
    regular, direct = one(r"Regular: ([0-9]+\.[0-9]+)% Direct: ([0-9]+\.[0-9]+)%$", expense)
    aum, average = one(
        r"ASSETS UNDER MANAGEMENT € As on "
        + label
        + r" Average for Month of September, 2026 ₹([0-9,]+\.[0-9]+)Cr\. ₹([0-9,]+\.[0-9]+)Cr\. QUANTITATIVE DATA",
        text,
    )
    managers = one(r"FUND MANAGER ¥ Name Since Total Exp (.*?) DATE OF ALLOTMENT", text)
    first, second = one(
        r"([A-Za-z ]+) February 01, 2026 Over 19 years ([A-Za-z ]+) \(Gold/Silver Instruments\) August 26,2026 Over 32 years",
        managers,
    )
    overseas = one(
        r"Dedicated Fund Manager for Overseas Investments: Mr\. ([A-Za-z ]+) \(since September 01, 2026\) \(Total Experience: Over 22 years\)",
        notes,
    )
    result = {}

    def accepted(name, value, unit, scope, raw, anchor):
        result[name] = MonthlyFact(
            status="accepted",
            value=value,
            unit=unit,
            scope=scope,
            disclosure_as_of=as_of,
            date_basis="document_context_not_field_effective_date",
            field_effective_date=None,
            raw_value=raw,
            source_anchor=anchor,
            reason=None,
        ).model_dump(mode="json")

    accepted(
        "benchmark_name",
        benchmark,
        "name",
        "fund",
        benchmark,
        "physical page 7: #BENCHMARK INDEX, not additional benchmark",
    )
    accepted(
        "base_expense_ratio",
        regular,
        "percent",
        "regular_plan",
        expense,
        "physical page 7: EXPENSE RATIO Regular, explicit BER label",
    )
    accepted(
        "aum",
        aum.replace(",", ""),
        "INR_crore",
        "fund",
        "₹" + aum + "Cr.",
        "physical page 7: As on September 30, 2026, not monthly average",
    )
    accepted(
        "fund_managers",
        [first, second, overseas],
        "names",
        "fund",
        managers + "; overseas: " + overseas,
        "physical pages 7-8: manager table plus dedicated overseas manager footnote; roles retained in snapshot",
    )
    result["risk_level"] = MonthlyFact(
        status="unavailable",
        value=None,
        unit=None,
        scope=None,
        disclosure_as_of=as_of,
        date_basis="document_context_not_field_effective_date",
        field_effective_date=None,
        raw_value=None,
        source_anchor=None,
        reason="riskometer_pages_not_parsed_no_gauge_or_SSD_date_inference",
    ).model_dump(mode="json")
    return result, {
        "fund_managers": [
            {"name": first, "role": "scheme_manager"},
            {"name": second, "role": "Gold/Silver Instruments"},
            {"name": overseas, "role": "dedicated_overseas_investments"},
        ],
        "direct_ber_not_selected": direct,
        "monthly_average_aum_not_selected": average,
    }


def derive(identity, book, summary):
    config = multi_facts.source()
    if identity != config["identity"]:
        raise ValueError("exact registered current HDFC identity required")
    for content, role in ((book, "edition"), (summary, "identity_source")):
        if hashlib.sha256(content).hexdigest() != config[role]["sha256"]:
            raise ValueError("registered original bytes differ; review new edition first")
    detail = monthly_pdf.extract(book, start=7, end=8)
    bridge = monthly_pdf.extract(summary, start=1, end=1)
    identity_evidence = idcw_discovery.registry_identity(identity, bridge["pages"][0]["text"])
    values, reported_context = fields(
        detail["pages"][0]["text"],
        detail["pages"][1]["text"],
        config["edition"]["disclosure_as_of"],
    )
    body = {
        "format": "registered-monthly-facts-v1",
        "source_key": config["key"],
        "code": identity["code"],
        "identity": identity,
        "identity_hash": fingerprint(identity),
        "content_hash": config["edition"]["sha256"],
        "identity_content_hash": config["identity_source"]["sha256"],
        "source_url": config["edition"]["url"],
        "identity_source_url": config["identity_source"]["url"],
        "disclosure_as_of": config["edition"]["disclosure_as_of"],
        "fields": values,
        "identity_evidence": identity_evidence,
        "reported_context": reported_context,
        "extraction": {
            **detail,
            "pages": [{k: v for k, v in p.items() if k != "text"} for p in detail["pages"]],
        },
        "identity_extraction": {
            **bridge,
            "pages": [{k: v for k, v in p.items() if k != "text"} for p in bridge["pages"]],
        },
        "policy_hash": fingerprint(multi_facts.policy()),
        "engine_hash": engine_hash(),
        "verification_status": "needs_verification",
        "canonical_analytics_replaced": False,
    }
    return {**body, "snapshot_id": fingerprint(body)}


def engine_hash():
    digest = hashlib.sha256()
    for name in (
        "hdfc_monthly.py",
        "multi_facts.py",
        "idcw_discovery.py",
        "monthly_pdf.py",
        "_monthly_pdf_worker.py",
        "monthly_schemas.py",
        "schemas.py",
        "evidence.py",
    ):
        content = Path(__file__).with_name(name).read_bytes()
        digest.update(name.encode() + b"\0" + len(content).to_bytes(8, "big") + content)
    return digest.hexdigest()
