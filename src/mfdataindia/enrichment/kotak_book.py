"""Kotak dated full-book/one-pager fields; filenames never supply dates."""

import calendar
import re
import unicodedata
from datetime import date
from decimal import Decimal

from mfdataindia.enrichment import (
    absl_expansion,
    amfi_summary_identity,
    layout_book_pdf,
    monthly_facts,
)
from mfdataindia.review.policy import fingerprint

KEY = "kotak-expanded"
AMC = "Kotak Mahindra Mutual Fund"
DIRECTORY = "https://www.amfiindia.com/api/populate-scheme?MF_ID=17"
CATALOGUE = "https://www.kotakmf.com/api/kotakapi/forms/user/getsubheaderList/219"
BOOK_PREFIX = "https://vatseelabs-s3.kotakmf.com/FormsDownloads/Factsheet/"
HISTORICAL = "https://www.kotakmf.com/factsheet/August_2026/kotak/Download_pdf/Kotak%20Nifty%2050%20Index%20Fund_Factsheet%20One%20Pager.pdf"
HISTORICAL_PDF_SHA256 = "b4a25f9b425c2d23380a1cbe7baacad4c2aa8da5300a8fd97111cf35ecec5126"
FIELDS = absl_expansion.FIELDS
MONTHS = {calendar.month_name[n].casefold(): n for n in range(1, 13)}
REVIEWED_TRI_LABELS = tuple(
    """Nifty 100 TRI
Nifty 500 TRI
Nifty Large Midcap 250 TRI
Nifty Midcap 150 TRI
Nifty Smallcap 250 TRI
Nifty Infrastructure TRI
Nifty 100 ESG Index TRI
Nifty India Manufacturing TRI
Nifty Financial Services Index TRI
Nifty 200 TRI
Nifty India Consumption Index TRI
BSE Teck Index TRI
Nifty MNC Index TRI
Nifty Equity Savings TRI
MSCI World Index TRI
BSE Sensex TRI
Nifty Alpha 50 Index TRI
Nifty 100 Low Volatility 30 TRI
Nifty 100 Equal Weight Index TRI
Nifty Midcap 150 Index TRI
Nifty200 Quality 30 Index TRI
Nifty Chemicals Index TRI
Nifty Private Bank Index TRI
Nifty 50 Index TRI
Nifty Smallcap 50 Index TRI
Nifty 200 Momentum 30 Index TRI
Nifty Financial Services Ex-Bank Index TRI
BSE Housing Index TRI
Nifty 100 Low Volatility 30 Index TRI
BSE PSU Index TRI
Nifty Midcap 50 Index TRI
Nifty India Tourism Index TRI
BSE Sensex Index TRI
Nifty Commodities Index TRI
Nifty Top 10 Equal Weight Index TRI
Nifty 200 Quality 30 Index TRI
Nifty200 Value 30 Index TRI
Nifty Alpha Low-Volatility 30 Index TRI""".splitlines()
)


def policy():
    return {
        "id": "official-kotak-monthly-book-facts",
        "version": 1,
        "fields": list(FIELDS),
        "identity": "actual AMFI directory and returned index/XML; exact code, checksum-valid ISIN, Regular option, every conflicting code claim checked; unique issuer heading or explicit erstwhile name",
        "decoder_policy_hash": fingerprint(layout_book_pdf.policy("kotak")),
        "periods": "two actual distinct source reporting dates, not necessarily consecutive; missing expected month explicit; source filename, URL, edition or PDF creation date never stamps facts",
        "historical_source": "one already-reviewed official Nifty 50 one-pager link; its actual July date is retained despite August URL; not whole-AMC prior-month coverage",
        "aum": "closing AUM only, explicit crs unit and same-page Data as on date covering all fields unless otherwise specified; AAUM, NAV date and folio date not substituted",
        "expenses": "Regular Plan Expense Ratio only when same-page footnote explicitly defines it as BER; no TER or Direct substitution",
        "benchmark": "complete explicit Benchmark block ending at Allotment Date; only an exact reviewed TRI label, optional explicit Tier 1; no portfolio text, partial index label, Tier 2 or PRI substitution",
        "reviewed_primary_tri_labels": list(REVIEWED_TRI_LABELS),
        "managers": "complete explicit same-fund manager block; no names from portfolio assets or general manager biographies",
        "risk": "explicit The risk of the scheme is sentence only; no benchmark risk or gauge inference",
        "population": "all retained current in-scope AMC variants, including unsupported and ambiguous identities as gaps",
        "verification": "needs_verification, no human decision generated",
        "whole_factsheet_semantic_coverage": False,
        "adjusted_idcw_analytics_enabled": False,
        "source_writes": "none; immutable evidence, reversible overlays only",
    }


def normalize(value):
    value = unicodedata.normalize("NFKC", value).casefold().replace("&", " and ")
    value = re.sub(r"\b(flexi|mid|small|large|multi)\s*[- ]?\s*cap\b", r"\1cap", value)
    return " ".join(re.sub(r"[^\w\s]", " ", value).split())


def left_column(text):
    columns = [
        line.index("Issuer/Instrument") for line in text.splitlines() if "Issuer/Instrument" in line
    ]
    if not columns or not 45 <= min(columns) <= 200:
        return None
    width = min(columns)
    return "\n".join(line[:width].strip() for line in text.splitlines())


def reporting_dates(text):
    matches = re.findall(
        r"(?im)^\s*\(?Data\s+as\s+on\s+([0-9]{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+),?\s+(20[0-9]{2})\b",
        text,
    )
    days = set()
    for day, month, year in matches:
        if month.casefold() not in MONTHS:
            raise ValueError("unrecognized explicit reporting month")
        value = date(int(year), MONTHS[month.casefold()], int(day))
        if value.day != calendar.monthrange(value.year, value.month)[1]:
            raise ValueError("explicit month-end reporting date required")
        days.add(value.isoformat())
    return days


def period(extraction, observed):
    dates = reporting_dates(extraction["pages"][0]["text"])
    if len(dates) != 1:
        raise ValueError("one actual first-page reporting date required")
    result = next(iter(dates))
    if date.fromisoformat(result) > observed.date():
        raise ValueError("future source reporting date")
    return result


def detail_pages(extraction):
    result = []
    for page in extraction["pages"]:
        left = left_column(page["text"])
        if (
            left is None
            or not re.search(r"(?m)^Fund Managers?\*?\s*:", left)
            or not re.search(r"(?m)^AUM\s*:", left)
        ):
            continue
        headings = re.findall(
            r"(?m)^KOTAK [A-Z0-9 &()/+-]{3,180}$", "\n".join(left.splitlines()[:7])
        )
        if len(headings) != 1:
            continue
        aliases = re.findall(
            r"\((?:ERSTWHILE KNOWN AS|FORMERLY(?: KNOWN)? AS) ([^)]{1,250})\)", left, re.I
        )
        result.append(
            {
                "names": {normalize(headings[0]), *(normalize(a) for a in aliases)},
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


def fields(detail, as_of):
    left, text = detail["left"], detail["page"]["text"]
    result = {key: missing(as_of, "explicit_field_anchor_missing_or_ambiguous") for key in FIELDS}
    if reporting_dates(left) != {as_of}:
        return {key: missing(as_of, "same_fund_reporting_context_unavailable") for key in FIELDS}

    def accept(key, value, unit, raw, anchor):
        result[key] = {
            "status": "accepted",
            "value": value,
            "unit": unit,
            "scope": "regular_plan" if key == "base_expense_ratio" else "fund",
            "reason": None,
            "disclosure_as_of": as_of,
            "field_effective_date": as_of if key == "aum" else None,
            "date_basis": "explicit_field_date"
            if key == "aum"
            else "document_context_not_field_effective_date",
            "raw_value": raw,
            "source_anchor": anchor,
        }

    aum = re.findall(r"(?mi)^AUM\s*:\s*[`₹]\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s+crs\s*$", left)
    if len(aum) == 1:
        accept(
            "aum",
            monthly_facts.decimal(aum[0], high=Decimal("1000000000000")),
            "INR_crore",
            aum[0],
            "AUM / crs / Data as on " + as_of,
        )
    benchmarks = re.findall(r"(?mis)^Benchmark\**\s*:\s*(.{1,700}?)(?=^Allotment Date\s*:)", left)
    benchmark = None
    if len(benchmarks) == 1 and not re.search(
        r"Benchmark|Fund Manager|AAUM|AUM\s*:", benchmarks[0], re.I
    ):
        candidate = " ".join(benchmarks[0].split())
        primary = re.fullmatch(
            r"(.{1,250}?\bTRI\s*\(Tier\s*1\))(?:\s*,\s*.{0,250}|\s+.{1,250}\(Tier\s*2\))?",
            candidate,
            re.I,
        )
        if primary:
            benchmark = primary[1]
        elif re.fullmatch(
            r"[A-Za-z0-9 &()/+.,-]{3,250}(?:\bTRI|\bIndex(?: [A-Z](?:-[IV]+)?)?)", candidate
        ) and not re.search(r"Tier", candidate, re.I):
            benchmark = candidate
    if benchmark is not None and re.sub(
        r"\s*\(Tier\s*1\)$", "", benchmark, flags=re.I
    ).casefold() not in {name.casefold() for name in REVIEWED_TRI_LABELS}:
        benchmark = None
    if benchmark is not None:
        accept(
            "benchmark_name",
            benchmark,
            "name",
            benchmark,
            "Benchmark / primary Tier 1 if specified",
        )
    costs = re.findall(r"(?mi)^Expense Ratio\**\s*\nRegular Plan\s*:\s*([0-9]+\.[0-9]+)%\s*$", left)
    if len(costs) == 1 and re.search(
        r"expense ratio disclosed above represents the Base Expense Ratio\s*\(BER\)",
        " ".join(text.split()),
        re.I,
    ):
        accept(
            "base_expense_ratio",
            monthly_facts.decimal(costs[0], high=Decimal("10")),
            "percent",
            costs[0],
            "Regular Plan / explicit Base Expense Ratio (BER) footnote",
        )
    managers = re.findall(
        r"(?mis)^Fund Managers?\*?\s*:\s*(.{1,1000}?)(?=^AAUM\s*:|^AUM\s*:)", left
    )
    if len(managers) == 1:
        raw = " ".join(managers[0].split()).strip(" ,")
        names = [
            n.strip(" ,") for n in re.split(r",?\s*(?=(?:Mr|Ms|Mrs)\.\s)", raw) if n.strip(" ,")
        ]
        if 1 <= len(names) <= 15 and all(
            re.fullmatch(r"(?:Mr|Ms|Mrs)\. [A-Z][A-Za-z]*(?:[ .'-]+[A-Z][A-Za-z]*)*\.?", n)
            and not re.search(r"\b(?:and|unknown|portfolio)\b", n, re.I)
            for n in names
        ):
            accept("fund_managers", names, "names", raw, "Fund Manager explicit complete block")
    risks = re.findall(
        r"The risk of the scheme is\s+(Low to Moderate|Moderately High|Very High|Moderate|High|Low)\b",
        " ".join(text.split()),
        re.I,
    )
    if len(risks) == 1:
        value = {
            x.casefold(): x
            for x in ("Low", "Low to Moderate", "Moderate", "Moderately High", "High", "Very High")
        }[risks[0].casefold()]
        accept("risk_level", value, "risk_category", risks[0], "The risk of the scheme is")
    return result


def derive(identity, bridges, details, as_of):
    def gap(reason):
        return {
            "status": "gap",
            "reason": reason,
            "fields": {key: missing(as_of, reason) for key in FIELDS},
        }

    if identity["amc"] != AMC:
        return gap("unsupported_current_identity")
    matches, reason = amfi_summary_identity.exact_matches(identity, bridges)
    if reason:
        return gap(reason)
    names = {normalize(r["fund_name"]) for r in matches}
    if len(names) != 1:
        return gap("issuer_family_identity_conflict")
    hits = [p for p in details if names & p["names"]]
    if len(hits) != 1:
        return gap("monthly_heading_missing_or_ambiguous")
    detail = hits[0]
    values = fields(detail, as_of)
    for value in values.values():
        value.update(
            content_hash=detail["source_hash"],
            source_url=detail["source_url"],
            detail_page=detail["page"]["page"],
            detail_text_hash=detail["page"]["text_hash"],
        )
    usable = any(v["status"] == "accepted" for v in values.values())
    return {
        "status": "usable" if usable else "gap",
        "reason": None if usable else "monthly_fields_unavailable",
        "fields": values,
        "identity_evidence": matches,
        "detail_page": detail["page"]["page"],
        "detail_text_hash": detail["page"]["text_hash"],
        "detail_content_hash": detail["source_hash"],
    }
