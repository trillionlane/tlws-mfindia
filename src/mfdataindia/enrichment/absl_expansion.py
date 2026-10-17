"""ABSL broader monthly facts: exact identifiers, explicit dates/units and gaps."""

import re
import unicodedata
from copy import deepcopy
from datetime import date

from mfdataindia.enrichment import amc_book_pdf, monthly_facts
from mfdataindia.enrichment.evidence import valid_isin
from mfdataindia.review.policy import fingerprint

AMC = "Aditya Birla Sun Life Mutual Fund"
FIELDS = ("benchmark_name", "fund_managers", "base_expense_ratio", "aum", "risk_level")


def policy():
    return {
        "id": "official-absl-expanded-monthly-facts",
        "version": 1,
        "identity": "issuer-authored exact AMFI-code/ISIN pair plus current database AMC/plan/option; matching fund heading or explicit same-page erstwhile name; no fuzzy/substring identities",
        "names": "Unicode NFKC/case/whitespace, ampersand/and, punctuation and cap-word spacing only; raw names retained; no semantic rename dictionary",
        "plan": "issuer Direct/Retail/Institutional labels never borrow Regular BER; a missing plan label withholds BER",
        "dates": "explicit month-end NAV date equals previous calendar month of official book edition; each field retains printed date or document-context date basis",
        "fields": list(FIELDS),
        "risk": "same-fund adjacent changes page, explicit Scheme Riskometer Previous/Current row; no pointer, legend, launch risk or benchmark risk",
        "expenses": "Regular Plan BER in percent, corroborated by same-fund change table; never relabelled as TER",
        "aum": "fund AUM only, explicit matching as-on date and Cr suffix; AAUM is not AUM",
        "managers": "complete printed manager block retained with source text; not inferred tenure or complete economic coverage",
        "publication": "dated usable fields or explicit per-fund/period/field gaps; gap never clears the last usable value",
        "population": "every current in_scope record under the exact registered AMC, including unsupported plan/option identities as explicit gaps",
        "verification": "needs_verification, independent from source publication",
        "source_writes": "none; append-only evidence and reversible current overlays",
        "whole_factsheet_semantic_coverage": False,
        "adjusted_idcw_analytics_enabled": False,
        "decoder_policy_hash": fingerprint(amc_book_pdf.policy()),
    }


def normalized_name(value):
    value = unicodedata.normalize("NFKC", value).casefold().replace("&", " and ")
    value = re.sub(r"\b(mid|small|large|multi)\s*[- ]?\s*cap\b", r"\1cap", value)
    return " ".join(re.sub(r"[^\w\s]", " ", value).split())


def summary_pages(extraction):
    """Keep paired label rows together. Reinvestment may share an AMFI code."""
    result = []
    for page in extraction["pages"]:
        text = page["text"]
        headings = re.findall(r"(?m)^1\s+Fund Name\s+(.+?)\s*$", text)
        if len(headings) != 1:
            continue
        sections = re.findall(
            r"(?s)\n(?:28\s+)?ISINs\s*\n(.*?)\n(?:29\s+)?AMFI Codes \(To be phased out\)\s*\n(.*?)\n30 SEBI Codes",
            text,
        )
        if len(sections) != 1:
            continue
        isins, codes = sections[0]
        isin_rows = re.findall(r"(?m)^(.+?)\s+([A-Z0-9]{12})\s*$", isins)
        code_rows = re.findall(r"(?m)^(.+?)\s+([1-9][0-9]*)\s*$", codes)
        left, right = {}, {}
        for label, isin in isin_rows:
            left.setdefault(normalized_name(label), []).append((label, isin))
        for label, code in code_rows:
            right.setdefault(normalized_name(label), []).append(int(code))
        # Some former variants explicitly have no ISIN. That is a gap for that
        # variant, not permission to invent one or discard independently paired
        # current identifiers. Ambiguous labels never produce a bridge.
        for label, records in left.items():
            if len(records) != 1 or len(right.get(label, [])) != 1:
                continue
            raw, isin = records[0]
            if not valid_isin(isin):
                continue
            result.append(
                {
                    "fund_name": headings[0],
                    "label": raw,
                    "isin": isin,
                    "code": right[label][0],
                    "page": page["page"],
                    "text_hash": page["text_hash"],
                    "source_hash": extraction["source_hash"],
                }
            )
    return result


def detail_pages(extraction):
    result = []
    for index, page in enumerate(extraction["pages"]):
        text = page["text"]
        if "Fund Details" not in text or text.count("Type of Scheme:") != 1:
            continue
        prefix = text.split("Type of Scheme:", 1)[0]
        titles = list(re.finditer(r"(?im)^Aditya Birla Sun Life\b", prefix))
        if not titles:
            continue
        heading = " ".join(prefix[titles[-1].start() :].split())
        aliases = re.findall(r"\(Erstwhile\s+([^)]{1,250})\)", heading, re.I)
        name = re.sub(r"\s*\(Erstwhile[^)]*\)", "", heading, flags=re.I).strip()
        if not 10 <= len(name) <= 250:
            continue
        changes = extraction["pages"][index + 1] if index + 1 < len(extraction["pages"]) else None
        result.append(
            {
                "name": name,
                "aliases": aliases,
                "detail": page,
                "changes": changes,
                "source_hash": extraction["source_hash"],
            }
        )
    return result


def bridge(identity, summaries):
    if (
        normalized_name(identity["amc"]) != normalized_name(AMC)
        or identity["plan"] != "REGULAR"
        or identity["option"] not in ("GROWTH", "IDCW")
    ):
        return None, "unsupported_current_identity"
    valid = {s for s in identity["isins"] if valid_isin(s)}
    matches = [r for r in summaries if r["code"] == identity["code"] and r["isin"] in valid]
    # A reinvestment and payout label may legitimately share a code. Every
    # matching row must identify the same fund and the current option/plan.
    if not matches:
        return None, "issuer_code_isin_bridge_missing"
    if len({normalized_name(r["fund_name"]) for r in matches}) != 1:
        return None, "issuer_code_isin_bridge_ambiguous"
    for row in matches:
        label = variant_suffix(row)
        if label is None:
            return None, "issuer_variant_label_not_exact"
        if " direct " in " " + label + " ":
            return None, "issuer_direct_plan_conflict"
        if any(word in label.split() for word in ("retail", "institutional")):
            return None, "issuer_non_regular_plan_conflict"
        options = {
            option
            for option, words in (("IDCW", ("idcw", "dividend")), ("GROWTH", ("growth",)))
            if any(word in label.split() for word in words)
        }
        observed = next(iter(options)) if len(options) == 1 else None
        if observed != identity["option"]:
            return None, "issuer_option_conflict"
    return matches, None


def variant_suffix(row):
    label, fund = normalized_name(row["label"]), normalized_name(row["fund_name"])
    return label[len(fund) :].strip() if label.startswith(fund + " ") else None


def fields(identity, detail, changes, *, as_of, regular_ber_allowed):
    result = {}

    def missing(reason):
        return {
            "status": "unavailable",
            "value": None,
            "unit": None,
            "scope": None,
            "reason": reason,
            "disclosure_as_of": as_of,
            "field_effective_date": None,
            "date_basis": "document_context_not_field_effective_date",
            "raw_value": None,
            "source_anchor": None,
        }

    def accept(value, unit, scope, raw, anchor, *, explicit=False):
        return {
            "status": "accepted",
            "value": value,
            "unit": unit,
            "scope": scope,
            "reason": None,
            "disclosure_as_of": as_of,
            "field_effective_date": as_of if explicit else None,
            "date_basis": "explicit_field_date"
            if explicit
            else "document_context_not_field_effective_date",
            "raw_value": raw,
            "source_anchor": anchor,
        }

    def one(pattern, source):
        values = re.findall(pattern, source, re.M)
        return values[0] if len(values) == 1 else None

    nav = one(r"^NAV as on ([A-Za-z]+ [0-9]{1,2},\s*[0-9]{4})\s*$", detail)
    if not nav or monthly_facts.english_date(nav) != as_of:
        raise ValueError("exact monthly NAV as-on date required")
    period = nav.split()[0] + " " + str(date.fromisoformat(as_of).year)
    headings = [line.strip() for line in changes.splitlines()]
    same_changes = period in headings and any(
        normalized_name(s) == normalized_name(identity["scheme_name"]) for s in headings
    )
    benchmark_raw = (
        one(r"^Benchmark Name ([\s\S]{1,600}?)\nNAV as on", detail)
        if len(re.findall(r"(?m)^Benchmark Name ", detail)) == 1
        else None
    )
    benchmark = " ".join(benchmark_raw.split()) if benchmark_raw else None
    result["benchmark_name"] = (
        accept(benchmark, "name", "fund", benchmark, "Fund Details / Benchmark Name")
        if benchmark and len(benchmark) <= 250
        else missing("benchmark_anchor_missing_or_ambiguous")
    )
    manager = one(r"Name of Fund Managers\s*\n([\s\S]{1,2500}?)\nTotal Experience", detail)
    names = (
        [s.strip().removeprefix("•").strip() for s in manager.splitlines() if s.strip()]
        if manager
        else []
    )
    result["fund_managers"] = (
        accept(names, "names", "fund", manager, "Fund Manager Details / Name of Fund Managers")
        if 1 <= len(names) <= 15 and all(len(s) <= 150 for s in names)
        else missing("manager_block_missing_or_ambiguous")
    )
    ber = one(r"^Regular Plan: BER ([0-9]{1,2}\.[0-9]{1,4})%\s*$", detail)
    change = (
        one(r"^Regular ([0-9]{1,2}\.[0-9]{1,4})% ([0-9]{1,2}\.[0-9]{1,4})%\s*$", changes)
        if same_changes
        else None
    )
    result["base_expense_ratio"] = missing("regular_BER_not_independently_corroborated")
    if (
        regular_ber_allowed
        and ber
        and change
        and monthly_facts.decimal(ber) == monthly_facts.decimal(change[1])
    ):
        from decimal import Decimal

        value = monthly_facts.decimal(ber, high=Decimal("10"))
        result["base_expense_ratio"] = accept(
            value,
            "percent",
            "regular_plan",
            ber + "%",
            "Expense Ratio / Regular Plan: BER; changes / Regular Current Month",
        )
    aum = one(r"^AUM as on ([A-Za-z]+ [0-9]{1,2},\s*[0-9]{4}) ([0-9,]+\.[0-9]+) Cr\s*$", detail)
    result["aum"] = (
        accept(
            monthly_facts.decimal(aum[1]),
            "INR_crore",
            "fund",
            aum[1] + " Cr",
            "Fund Details / AUM",
            explicit=True,
        )
        if aum and monthly_facts.english_date(aum[0]) == as_of
        else missing("AUM_units_date_or_anchor_unavailable")
    )
    risk = (
        one(
            r"^Scheme Riskometer (" + monthly_facts.RISK + r") (" + monthly_facts.RISK + r")\s*$",
            changes,
        )
        if same_changes and "Changes in Risk-o-Meter of scheme" in changes
        else None
    )
    result["risk_level"] = (
        accept(
            risk[1],
            "risk_category",
            "fund",
            " ".join(risk),
            "changes / Scheme Riskometer / Current Month",
        )
        if risk
        else missing("explicit_same_fund_current_risk_unavailable")
    )
    return result


def derive_one(identity, summaries, details, *, as_of):
    matched, gap = bridge(identity, summaries)
    if gap:
        return {"status": "gap", "reason": gap, "fields": {}, "identity_evidence": None}
    names = {normalized_name(identity["scheme_name"])}
    candidates = [
        d
        for d in details
        if normalized_name(d["name"]) in names
        and normalized_name(matched[0]["fund_name"])
        in {normalized_name(d["name"]), *(normalized_name(a) for a in d["aliases"])}
    ]
    if len(candidates) != 1:
        return {
            "status": "gap",
            "reason": "exact_monthly_identity_missing_or_ambiguous",
            "fields": {},
            "identity_evidence": matched,
        }
    selected = candidates[0]
    labels = [variant_suffix(r) for r in matched]
    regular = all(
        "regular" in s.split()
        and not any(x in s.split() for x in ("retail", "institutional", "direct"))
        for s in labels
    )
    try:
        values = fields(
            identity,
            selected["detail"]["text"],
            selected["changes"]["text"] if selected["changes"] else "",
            as_of=as_of,
            regular_ber_allowed=regular,
        )
    except (ValueError, TypeError):
        return {
            "status": "gap",
            "reason": "monthly_layout_or_date_differs",
            "fields": {},
            "identity_evidence": matched,
        }
    usable = any(f["status"] == "accepted" for f in values.values())
    return {
        "status": "usable" if usable else "gap",
        "reason": None if usable else "no_usable_supported_field",
        "fields": deepcopy(values),
        "identity_evidence": matched,
        "detail_page": selected["detail"]["page"],
        "detail_text_hash": selected["detail"]["text_hash"],
        "changes_page": selected["changes"]["page"] if selected["changes"] else None,
        "changes_text_hash": selected["changes"]["text_hash"] if selected["changes"] else None,
        "name_evidence": {"monthly": selected["name"], "erstwhile": selected["aliases"]},
    }
