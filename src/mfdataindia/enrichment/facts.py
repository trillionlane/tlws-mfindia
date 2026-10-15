"""Bounded ABSL disclosure adapter. No source edits, fuzzy identity or ratio substitution."""
from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, localcontext
from pathlib import Path

from mfdataindia.enrichment.evidence import normalized, valid_isin
from mfdataindia.enrichment.pdf_evidence import extract_pdf
from mfdataindia.review.policy import fingerprint

AMC = "Aditya Birla Sun Life Mutual Fund"
_POLICY = {
    "id": "official-absl-fund-facts", "version": 1, "engine_version": "absl-facts-1",
    "scope": "ABSL regular Growth/IDCW; explicit bounded pages; no other AMC/layout fallback",
    "identity": "exact normalized fund name; issuer summary pairs identical variant labels with AMFI code and checksum-valid payout ISIN; current DB AMC/name/plan/option/ISIN must agree",
    "regular_growth_label": "issuer summary suffix -Growth; direct variants are distinct and never matched",
    "idcw_label": "issuer summary suffix -Regular - IDCW; factsheet must explicitly list Regular Plan - Annual IDCW Option",
    "dates": "factsheet NAV as-on date must equal declared document_as_of; other fields explicitly document-context dated, not independently field-effective dated",
    "fields": ["benchmark_name", "fund_managers", "base_expense_ratio", "aum", "total_expense_ratio"],
    "expenses": "BER published as BER only, in percent; no conversion to TER; decimal text without binary float",
    "aum": "unavailable when detail page does not state units; no assumed crore conversion",
    "conflicts": "same fund/as-of distinct source/identity documents retained; current read withholds rather than selects by ingestion order",
    "current": "latest disclosure as-of among retained snapshots, not freshness/completeness certification; changed identity withholds fields",
    "publication": "official disclosures only; not calculated analytics, benchmark mapping or IDCW eligibility",
    "maximum_batch": 10, "maximum_page": 400,
    "rounding": "none; preserve finite decimal token exactly; precision 50 for validation",
    "source_writes": "none; immutable sidecars and original source evidence only",
}


def policy():
    return deepcopy(_POLICY)


def one(pattern, text, *, flags=0):
    values = re.findall(pattern, text, flags)
    if len(values) != 1:
        raise ValueError("missing or ambiguous official disclosure anchor")
    return values[0]


def decode(content, page):
    result = extract_pdf(content, page_start=page, page_end=page)
    return result["pages"][0]["text"], {
        "extractor_version": result["extractor_version"],
        "extraction_policy_hash": result["policy_hash"],
        "extraction_engine_hash": result["engine_code_hash"],
        "text_hash": result["pages"][0]["text_hash"],
        "page": page,
    }


def derive(identity, disclosure, summary, *, as_of, facts_page, identity_page):
    """Derive only from original bytes; tests may exercise pure parse separately."""
    detail, detail_lineage = decode(disclosure, facts_page)
    registry, identity_lineage = decode(summary, identity_page)
    return parse(identity, detail, registry, as_of=as_of,
                 detail_lineage=detail_lineage, identity_lineage=identity_lineage)


def parse(identity, detail, registry, *, as_of, detail_lineage, identity_lineage):
    if normalized(identity["amc"]) != normalized(AMC) or identity["plan"] != "REGULAR" or identity["option"] not in ("GROWTH", "IDCW"):
        raise ValueError("unsupported exact AMC/plan/option")
    name = identity["scheme_name"]
    if normalized(one(r"(?m)^1 Fund Name (.+?)\s*$", registry)) != normalized(name):
        raise ValueError("issuer summary fund identity differs")
    # Two appearances are normal in this layout (title and riskometer); require
    # an exact whole-line title, not a substring of another fund's name.
    if normalized(name) not in {normalized(line) for line in detail.splitlines()}:
        raise ValueError("factsheet exact fund heading missing")
    isin_section = one(r"(?s)\nISINs\s*\n(.*?)\nAMFI Codes \(To be phased out\)\s*\n", registry)
    code_section = one(r"(?s)\nAMFI Codes \(To be phased out\)\s*\n(.*?)\n30 SEBI Codes", registry)
    isin_rows = re.findall(r"(?m)^(.+?) ([A-Z0-9]{12})\s*$", isin_section)
    code_rows = re.findall(r"(?m)^(.+?) ([1-9][0-9]*)\s*$", code_section)
    for rows, section in ((isin_rows, isin_section), (code_rows, code_section)):
        if not rows or len(rows) != len([line for line in section.splitlines() if line.strip()]) or len({normalized(k) for k, _ in rows}) != len(rows) or len({v for _, v in rows}) != len(rows):
            raise ValueError("ambiguous issuer identifier rows")
    if {normalized(k) for k, _ in isin_rows} != {normalized(k) for k, _ in code_rows}:
        raise ValueError("issuer identifier sections disagree")
    suffix = "-Growth" if identity["option"] == "GROWTH" else "-Regular - IDCW"
    label = name + suffix
    pattern = r"(?m)^" + re.escape(label) + r" ([A-Z0-9]{12})\s*$"
    isin = one(pattern, isin_section)
    code = one(r"(?m)^" + re.escape(label) + r" ([1-9][0-9]*)\s*$", code_section)
    if not valid_isin(isin) or isin not in identity["isins"] or int(code) != identity["code"]:
        raise ValueError("issuer AMFI code/ISIN differs from current identity")
    option_label = "Growth" if identity["option"] == "GROWTH" else "Annual IDCW"
    one(r"(?m)^Regular Plan - " + option_label + r" Option ₹ [0-9]+\.[0-9]+\s*$", detail)
    date_token = one(r"(?m)^NAV as on ([A-Za-z]+ [0-9]{1,2}, [0-9]{4})\s*$", detail)
    observed_date = datetime.strptime(date_token, "%B %d, %Y").date().isoformat()
    if observed_date != as_of:
        raise ValueError("factsheet as-on date differs")
    benchmark = one(r"(?m)^Benchmark Name (.+?)\s*$", detail)
    managers = one(r"(?s)Name of Fund Managers\s*\n(.*?)\nTotal Experience", detail)
    manager_names = [line.strip().removeprefix("•").strip() for line in managers.splitlines() if line.strip()]
    if not 1 <= len(manager_names) <= 10 or any(len(x) > 150 for x in manager_names):
        raise ValueError("unsupported manager block")
    raw_ber = one(r"(?m)^Regular Plan: BER ([0-9]{1,2}\.[0-9]{1,4})%\s*$", detail)
    with localcontext() as context:
        context.prec = 50
        ber = Decimal(raw_ber)
        if not ber.is_finite() or not 0 <= ber <= 10:
            raise ValueError("expense disclosure outside supported bounds")
    def field(value, unit, scope, raw, anchor):
        return {"status": "accepted", "value": value, "unit": unit, "scope": scope,
                "disclosure_as_of": as_of, "date_basis": "document_context_not_field_effective_date",
                "raw_value": raw, "source_anchor": anchor, "reason": None}
    fields = {
        "benchmark_name": field(benchmark, "name", "fund", benchmark, "Fund Details / Benchmark Name"),
        "fund_managers": field(manager_names, "names", "fund", managers.strip(), "Fund Manager Details / Name of Fund Managers"),
        "base_expense_ratio": field(format(ber, "f"), "percent", "regular_plan", raw_ber + "%", "Expense Ratio / Regular Plan: BER"),
    }
    for key, reason in (("aum", "units_not_explicit_on_detail_page"), ("total_expense_ratio", "BER_is_not_TER")):
        fields[key] = {"status": "unavailable", "value": None, "unit": None, "scope": None,
                       "disclosure_as_of": as_of, "date_basis": "document_context_not_field_effective_date",
                       "raw_value": None, "source_anchor": None, "reason": reason}
    return {"fields": fields, "identity_evidence": {"variant_label": label, "amfi_code": int(code),
            "matched_payout_isin": isin, "reinvestment_isin_verified": False},
            "disclosure_extraction": detail_lineage, "identity_extraction": identity_lineage,
            "engine_code_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "policy_hash": fingerprint(policy())}
