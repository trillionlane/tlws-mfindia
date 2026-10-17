"""UTI English monthly books; dated facts, never TER-as-BER or edition dates."""

import calendar
import re
import unicodedata
from datetime import date
from decimal import Decimal

from mfdataindia.enrichment import (
    absl_expansion,
    amfi_summary_identity,
    uti_book_pdf,
    monthly_facts,
)
from mfdataindia.review.policy import fingerprint

KEY = "uti-expanded"
AMC = "UTI Mutual Fund"
DIRECTORY = "https://www.amfiindia.com/api/populate-scheme?MF_ID=28"
CATALOGUE = "https://www.utimf.com/api/get-fact-sheet"
CDN = "https://d3ce1o48hc5oli.cloudfront.net/"
FIELDS = absl_expansion.FIELDS
MONTHS = {calendar.month_name[n].casefold(): n for n in range(1, 13)}
MANAGER_HEADER = r"Fund Manager(?:[ \t]{8,}[A-Za-z]{1,3})?"


def policy():
    return {
        "id": "official-uti-english-monthly-book-facts",
        "version": 1,
        "fields": list(FIELDS),
        "supplemental_fields": ["reported_total_expense_ratio"],
        "language": "exact issuer English active/passive row selected at catalogue boundary; no Hindi fallback or translation",
        "identity": "exact returned AMFI code, checksum-valid ISIN, explicit Regular option and complete conflicting code claims; unique same-fund source heading or explicit source-authored Erstwhile name, never database name similarity",
        "decoder_policy_hash": fingerprint(uti_book_pdf.policy()),
        "periods": "two actual Portfolio as on month-ends, with both active/passive originals per period; not edition, URL, metadata or filename dates",
        "aum": "Closing AUM only with explicit INR crore unit and Fund AUM/Folio as on date equal to the reporting period; dated discrepancies retained, not repaired",
        "expenses": "UTI month-end Total Expense Ratio is reported separately as TER; never a base_expense_ratio or core BER override",
        "benchmark": "complete explicit Benchmark Index physical-column block; preserve the official name without PRI/TRI renaming; no adjacent holdings, numbers alone or performance-table substitution",
        "benchmark_price_series_provided": False,
        "managers": "all explicit titled names in complete Fund Manager block, with a qualification delimiter; never biographies, partial wraps or former-manager blocks",
        "risk": "only unique same-fund first-page explicit scheme-risk caption and explicit matching portfolio-ending date; no gauge inference, reference-page pointer or other-fund cover borrowing",
        "verification": "needs_verification; no human decision generated",
        "whole_factsheet_semantic_coverage": False,
        "adjusted_idcw_analytics_enabled": False,
        "source_writes": "none; immutable evidence and reversible existing projections only",
    }


def normalize(value):
    value = unicodedata.normalize("NFKC", value).casefold().replace("&", " and ")
    value = re.sub(r"\b(flexi|mid|small|large|multi)\s*[- ]?\s*cap\b", r"\1cap", value)
    return " ".join(re.sub(r"[^\w\s]", " ", value).split())


def parse_date(day, month, year):
    if month.casefold() not in MONTHS:
        raise ValueError("explicit reporting month required")
    result = date(int(year), MONTHS[month.casefold()], int(day))
    if result.day != calendar.monthrange(result.year, result.month)[1]:
        raise ValueError("month-end reporting date required")
    return result.isoformat()


def labelled_dates(text, label):
    return {
        parse_date(*m)
        for m in re.findall(
            re.escape(label) + r"\s+([0-9]{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+),?\s+(20[0-9]{2})\b",
            text,
            re.I,
        )
    }


def period(extractions, observed):
    dates = {
        day
        for extraction in extractions
        for page in extraction["pages"]
        for day in labelled_dates(page["text"], "Portfolio as on")
    }
    if len(dates) != 1:
        raise ValueError("one actual shared active/passive Portfolio as on date required")
    result = next(iter(dates))
    if date.fromisoformat(result) > observed.date():
        raise ValueError("future reporting date")
    return result


def detail_pages(extraction):
    result = []
    for page in extraction["pages"]:
        left = page.get("physical_left_text")
        if left is None:
            continue
        header = "\n".join(
            re.split(r"\s{4,}", line.strip(), maxsplit=1)[0]
            for line in page["text"].splitlines()[:5]
        )
        headings = re.findall(
            r"(?m)^(UTI [A-Z0-9 &()/+:-]{3,200}?(?:FUND|ETF|PLAN))(?:[ *@]*)(?=\s+\(|$)", header
        )
        if (
            len(headings) != 1
            or not re.search(r"\b(?:FUND|ETF)\b", headings[0])
            or not re.search(r"(?m)^" + MANAGER_HEADER + r"\s*$", left)
        ):
            continue
        names = {normalize(headings[0])}
        title_line = next(line for line in header.splitlines() if line.startswith(headings[0]))
        tail = title_line[len(headings[0]) :].strip(" *@")
        if (
            tail.startswith("(Erstwhile ")
            and tail.endswith(")")
            and len(tail) <= 300
            and tail.count("(") == tail.count(")")
        ):
            names.add(normalize(tail[len("(Erstwhile ") : -1]))
        result.append(
            {
                "name": normalize(headings[0]),
                "names": names,
                "page": page,
                "left": left,
                "source_hash": extraction["source_hash"],
            }
        )
    return result


def missing(as_of, reason):
    return {
        "status": "unavailable",
        "value": None,
        "unit": None,
        "scope": None,
        "reason": reason,
        "disclosure_as_of": as_of,
        "field_effective_date": None,
        "date_basis": "no_usable_field_evidence",
        "raw_value": None,
        "source_anchor": None,
    }


def cover_risk(extraction, name, as_of):
    text = extraction["pages"][0]["text"]
    names = {
        normalize(line.strip())
        for line in text.splitlines()
        if re.fullmatch(r"\s*UTI [A-Za-z0-9 &()/+-]{3,200} Fund\s*", line, re.I)
    }
    dates = {
        parse_date(day, month, year)
        for month, day, year in re.findall(
            r"portfolio ending\s+([A-Za-z]+)\s+([0-9]{1,2}),?\s+(20[0-9]{2})", text, re.I
        )
    }
    risks = re.findall(
        r"The risk of the scheme is\s+(Low to Moderate|Moderately High|Very High|Moderate|High|Low)\b",
        " ".join(text.split()),
        re.I,
    )
    if names != {name} or dates != {as_of} or len(risks) != 1:
        return None
    value = {
        r.casefold(): r
        for r in ("Low", "Low to Moderate", "Moderate", "Moderately High", "High", "Very High")
    }[risks[0].casefold()]
    return value, risks[0], extraction["pages"][0]


def fields(detail, extraction, as_of):
    left = detail["left"]
    values = {
        key: missing(as_of, "explicit_same_fund_field_evidence_missing_or_ambiguous")
        for key in FIELDS
    }
    values["base_expense_ratio"] = missing(as_of, "no_explicit_BER_evidence_TER_not_substituted")
    supplemental = missing(as_of, "explicit_regular_month_end_TER_unavailable")
    discrepancies = []
    if labelled_dates(detail["page"]["text"], "Portfolio as on") != {as_of}:
        return values, supplemental, discrepancies

    def accepted(value, unit, raw, anchor, *, scope="fund", effective=None, page=None):
        page = page or detail["page"]
        return {
            "status": "accepted",
            "value": value,
            "unit": unit,
            "scope": scope,
            "reason": None,
            "disclosure_as_of": as_of,
            "field_effective_date": effective,
            "date_basis": "explicit_field_date"
            if effective
            else "document_context_not_field_effective_date",
            "raw_value": raw,
            "source_anchor": anchor,
            "content_hash": detail["source_hash"],
            "detail_page": page["page"],
            "detail_text_hash": page["text_hash"],
            "source_url": detail["source_url"],
        }

    benchmarks = re.findall(
        r"(?mis)^Benchmark Index\s*\n(.{1,250}?)(?=^" + MANAGER_HEADER + r"\s*$)", left
    )
    if len(benchmarks) == 1:
        raw = " ".join(benchmarks[0].split())
        if (
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 &()/+.,:%-]{1,249}", raw)
            and re.search(r"[A-Za-z]", raw)
            and raw.casefold() not in {"not applicable", "n/a", "na", "not available"}
            and not re.search(
                r"\b(?:Ltd|Limited|AUM|NAV|Regular|Direct|Portfolio)\b|Fund Manager|Total Expense|Tracking Error",
                raw,
                re.I,
            )
        ):
            values["benchmark_name"] = accepted(
                raw, "name", raw, "Benchmark Index explicit complete block"
            )
    managers = re.findall(r"(?mis)^" + MANAGER_HEADER + r"\s*\n(.{1,3000}?)(?=^Plans/Option)", left)
    if len(managers) == 1 and not re.search(
        r"\b(?:upto|until|ceased|formerly)\b", managers[0], re.I
    ):
        block = managers[0]
        names = re.findall(
            r"(?m)^((?:Mr\.?|Mrs\.?|Ms\.?|Dr\.?)\s+[A-Z][A-Za-z]*(?: +[A-Z][A-Za-z]*){1,4})\s*(?=[,(-])",
            block,
        )
        count = len(re.findall(r"(?m)^(?:Mr\.?|Mrs\.?|Ms\.?|Dr\.?)\s+", block))
        if 1 <= len(names) == count <= 15 and len(set(names)) == len(names):
            values["fund_managers"] = accepted(
                names,
                "names",
                " | ".join(names),
                "Fund Manager complete titled names before qualification delimiters",
            )
    aum_dates = labelled_dates(left, "Fund AUM/Folio as on")
    amounts = re.findall(
        r"(?mi)^Closing AUM\s*:\s*[`₹]\s*(?:Crore\s*([0-9][0-9,]*(?:\.[0-9]+)?)|([0-9][0-9,]*(?:\.[0-9]+)?)\s+Crore)\s*$",
        left,
    )
    amounts = [a or b for a, b in amounts]
    if len(amounts) == 1:
        if aum_dates == {as_of}:
            values["aum"] = accepted(
                monthly_facts.decimal(amounts[0], high=Decimal("1000000000000")),
                "INR_crore",
                amounts[0],
                "Closing AUM / INR Crore / explicit same-period Fund AUM/Folio as on",
                effective=as_of,
            )
        else:
            values["aum"] = missing(
                as_of, "explicit_AUM_date_missing_or_differs_from_reporting_period"
            )
            discrepancies.append(
                {
                    "field": "aum",
                    "reported_period": as_of,
                    "explicit_field_dates": sorted(aum_dates),
                    "raw_value": amounts[0],
                    "action": "withheld_not_restamped_or_repaired",
                }
            )
    costs = re.findall(
        r"(?mi)^Month-end Total Expense Ratio \(%\)\*?\s*\n\s*Regular\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*$",
        left,
    )
    if len(costs) == 1:
        values["base_expense_ratio"] = missing(as_of, "source_reports_TER_not_BER")
        supplemental = accepted(
            monthly_facts.decimal(costs[0], high=Decimal("10")),
            "percent",
            costs[0],
            "Month-end Total Expense Ratio (%) / Regular",
            scope="regular_plan",
        )
        supplemental["expense_basis"] = "TER"
    risk = cover_risk(extraction, detail["name"], as_of)
    if risk:
        value, raw, page = risk
        values["risk_level"] = accepted(
            value,
            "risk_category",
            raw,
            "Unique same-fund cover / The risk of the scheme is / explicit portfolio ending date",
            page=page,
            effective=as_of,
        )
    return values, supplemental, discrepancies


def derive(identity, bridges, details, extractions, as_of):
    def gap(reason):
        return {
            "status": "gap",
            "reason": reason,
            "fields": {key: missing(as_of, reason) for key in FIELDS},
            "reported_total_expense_ratio": missing(as_of, reason),
            "date_discrepancies": [],
        }

    if identity["amc"] != AMC:
        return gap("unsupported_current_identity")
    matches, reason = amfi_summary_identity.exact_matches(identity, bridges)
    if reason:
        return gap(reason)
    names = {normalize(r["fund_name"]) for r in matches}
    hits = [d for d in details if d["names"] & names]
    if len(names) != 1 or len(hits) != 1:
        return gap("monthly_heading_missing_or_ambiguous")
    detail = hits[0]
    extraction = next(e for e in extractions if e["source_hash"] == detail["source_hash"])
    values, ter, discrepancies = fields(detail, extraction, as_of)
    usable = any(v["status"] == "accepted" for v in values.values())
    return {
        "status": "usable" if usable else "gap",
        "reason": None if usable else "monthly_fields_unavailable",
        "fields": values,
        "reported_total_expense_ratio": ter,
        "date_discrepancies": discrepancies,
        "identity_evidence": matches,
        "detail_page": detail["page"]["page"],
        "detail_text_hash": detail["page"]["text_hash"],
        "detail_content_hash": detail["source_hash"],
    }
