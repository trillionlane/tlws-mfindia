"""SBI monthly evidence. XML identifies variants; only dated books supply facts.

No stored source is repaired, no name-only join is accepted, and neither launch
risk nor undated scheme-summary values can become monthly observations.
"""

import calendar
import hashlib
import json
import re
from datetime import date
from decimal import Decimal
from html.parser import HTMLParser
from urllib.parse import urljoin

from defusedxml import ElementTree

from mfdataindia.enrichment import absl_expansion, amc_book_pdf, monthly_facts
from mfdataindia.enrichment.evidence import valid_isin
from mfdataindia.review.policy import fingerprint

AMC = "SBI Mutual Fund"
KEY = "sbi-expanded"
CATALOGUE = "https://www.sbimf.com/ajaxcall/CMS/GetRecentFactSheets"
DIRECTORY = "https://www.amfiindia.com/api/populate-scheme?MF_ID=22"
FIELDS = absl_expansion.FIELDS
normalize = absl_expansion.normalized_name
MONTHS = {calendar.month_name[n].casefold(): n for n in range(1, 13)}
BOOK = re.compile(
    r"https://www\.sbimf\.com/docs/default-source/scheme-factsheets/"
    r"(?P<kind>all-sbimf-schemes|sbi-mf-passives-\(index-etf-fof\))-factsheet-"
    r"(?P<month>[a-z]+)-(?P<year>20[0-9]{2})\.pdf(?:\?sfvrsn=[A-Za-z0-9_]+)?"
)


def policy():
    return {
        "id": "official-sbi-expanded-monthly-facts",
        "version": 1,
        "identity": "AMFI public SBI directory and returned document index; issuer XML exact code/checksum-valid ISIN/Regular option plus unique monthly heading or explicit formerly name; names discover only",
        "xml": "bounded defusedxml; DTD, entities and external references forbidden; XML is identity evidence only",
        "content_aliases": "one immutable original per hash with its first URL preserved; every actually acquired alias remains a sealed URL/parent edge; byte-identical official URLs are not different originals",
        "books": "one AMC batch per period, combining distinct active/passive originals; latest two complete nonfuture month-end reporting periods",
        "fields": list(FIELDS),
        "benchmark": "unique explicitly labelled Benchmark Index or Ready Reckoner Benchmark column; no performance-table benchmark inference",
        "expenses": "explicit Regular BER column/Base Expense Ratio label, or first member of explicitly labelled Regular-plan BER/TER pair; never TER, Direct or changes-table TER",
        "ready_reckoner": "fallback only when no primary detail for the exact family; explicit Scheme Name/Benchmark/AUM(Rs.Crs)/Regular BER-TER/Direct BER-TER headers, unambiguous row and dated AUM footnote; only three fields, no invented manager or risk",
        "aum": "fund AUM, explicit exact date and Cr/Crores; never AAUM or inferred currency units",
        "risk": "explicit sentence The risk of the scheme is; no benchmark, launch risk, gauge-pointer or legend inference",
        "managers": "complete explicitly labelled name block, wrapped names and co-manager role retained; ambiguous blocks unavailable",
        "dates": "explicit actual field date takes precedence; context date is labelled, not fabricated field date",
        "population": "all current in_scope variants of exact AMC, including unsupported or ambiguous identities as gaps",
        "verification": "needs_verification; no human decisions generated",
        "whole_factsheet_semantic_coverage": False,
        "adjusted_idcw_analytics_enabled": False,
        "source_writes": "none; immutable originals and reversible overlays only",
        "decoder_policy_hash": fingerprint(amc_book_pdf.policy()),
    }


class Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = set()

    def handle_starttag(self, tag, attributes):
        values = dict(attributes)
        if tag == "a" and values.get("href"):
            self.links.add(urljoin("https://www.sbimf.com/", values["href"]))


def editions(content, observed):
    if not isinstance(content, bytes) or not 0 < len(content) <= 1048576:
        raise ValueError("bounded SBI catalogue required")
    parser = Links()
    parser.feed(content.decode("utf-8"))
    periods = {}
    for url in sorted(parser.links):
        match = BOOK.fullmatch(url)
        if not match or match["month"] not in MONTHS:
            continue
        year, month = int(match["year"]), MONTHS[match["month"]]
        as_of = date(year, month, calendar.monthrange(year, month)[1])
        if as_of > observed.date():
            raise ValueError("future SBI reporting period")
        kind = "active" if match["kind"] == "all-sbimf-schemes" else "passive"
        candidates = periods.setdefault(as_of.isoformat(), {})
        if kind in candidates:
            raise ValueError("ambiguous SBI edition")
        candidates[kind] = url
    # A partial newer edition must not be misrepresented by silently choosing
    # an older complete pair as the newest available period.
    selected = sorted(periods, reverse=True)[:2]
    if len(selected) != 2 or any(set(periods[p]) != {"active", "passive"} for p in selected):
        raise ValueError("two latest complete SBI active/passive periods required")
    return [{"as_of": p, "documents": periods[p]} for p in selected]


def directory(content):
    rows = json.loads(content)
    if not isinstance(rows, list) or not 1 <= len(rows) <= 4096:
        raise ValueError("bounded AMFI SBI scheme directory required")
    result = []
    for row in rows:
        sid, name = str(row.get("scheme_id", "")), row.get("scheme_name")
        if (
            not re.fullmatch(r"[1-9][0-9]{0,7}", sid)
            or not isinstance(name, str)
            or not 1 <= len(name) <= 350
        ):
            raise ValueError("AMFI scheme directory shape differs")
        result.append({"scheme_id": sid, "scheme_name": name})
    if len({r["scheme_id"] for r in result}) != len(result):
        raise ValueError("ambiguous AMFI scheme directory")
    return result


def identity_rows(content, source_url, index_hash):
    if not isinstance(content, bytes) or not 0 < len(content) <= 1048576:
        raise ValueError("bounded original XML required")
    root = ElementTree.fromstring(
        content, forbid_dtd=True, forbid_entities=True, forbid_external=True
    )
    if root.tag != "root":
        return []  # Other SSD layouts remain explicit identity gaps in v1.
    names = root.findall("Fund_Name")
    if len(names) != 1 or not names[0].text or len(names[0].text) > 350:
        return []
    digest = hashlib.sha256(content).hexdigest()
    rows = []
    for position, item in enumerate(root.findall("./Options_Names/item"), 1):

        def value(tag):
            found = item.findall(tag)
            return found[0].text.strip() if len(found) == 1 and found[0].text else ""

        code, isin, label = value("AMFICode"), value("ISIN"), value("OptionsNames")
        if (
            not re.fullmatch(r"[1-9][0-9]{4,7}", code)
            or not valid_isin(isin)
            or not 1 <= len(label) <= 350
        ):
            continue
        rows.append(
            {
                "fund_name": names[0].text.strip(),
                "code": int(code),
                "isin": isin,
                "label": label,
                "source_hash": digest,
                "source_url": source_url,
                "locator": "/root/Options_Names/item[" + str(position) + "]",
                "index_hash": index_hash,
            }
        )
    if len(rows) > 200:
        raise ValueError("XML identity variant bound exceeded")
    return rows


def family_names(value):
    # Only source-authored formerly/previously clauses, not a rename dictionary.
    parts = re.split(
        r"\((?:formerly|previously)(?:\s+known)?\s+as\s+([^)]{1,250})\)", value, flags=re.I
    )
    return {normalize(p.strip()) for p in parts if p.strip()}


def detail_pages(extraction):
    result = []
    for page in extraction["pages"]:
        text = page["text"]
        if not re.search(
            r"(?m)^Fund Details(?:\s+Fund Performance[^\n]*)?\s*$", text
        ) or not re.search(r"(?m)^Type of Scheme:", text):
            continue
        headings = re.findall(
            r"(?m)^SBI [A-Z][A-Z0-9 &()/+-]{4,150}(?:\n(?:[A-Z][A-Z &()/+-]{3,80}))?\s*$", text
        )
        headings = [" ".join(h.split()) for h in headings if "FUND" in h or "ETF" in h]
        if len(headings) != 1:
            continue
        aliases = re.findall(r"\((?:Formerly|Previously)(?: known)? as ([^)]{1,250})\)", text, re.I)
        result.append(
            {
                "names": {normalize(headings[0]), *(normalize(a) for a in aliases)},
                "page": page,
                "source_hash": extraction["source_hash"],
            }
        )
    primary_names = {name for record in result for name in record["names"]}
    result.extend(
        record for record in ready_rows(extraction) if not (record["names"] & primary_names)
    )
    return result


def ready_rows(extraction):
    """The older passive LITE edition is a table, not a fabricated detail page."""
    records = []
    for page in extraction["pages"]:
        text = page["text"]
        if (
            "Passive Investment Solutions - Ready Reckoner" not in text
            or "Scheme Name Inception Date Benchmark AUM (Rs. Crs)* BER/TER BER/TER" not in text
            or "Regular plan Direct plan" not in text
        ):
            continue
        dates = re.findall(
            r"\*AUM as on ([A-Za-z]+) ([0-9]{1,2})(?:st|nd|rd|th)?, ([0-9]{4})", text
        )
        if len(dates) != 1 or dates[0][0].casefold() not in MONTHS:
            continue
        month, day, year = dates[0]
        as_of = date(int(year), MONTHS[month.casefold()], int(day)).isoformat()
        pattern = (
            r"(?m)^(SBI[ A-Z0-9&()/+\-\n]{3,200}?)\s+"
            r"[0-3][0-9][/-][01][0-9][/-]20[0-9]{2}\s+"
            r"([^\n]{1,200}?)\s+([0-9][0-9,]*(?:\.[0-9]+)?)\s+"
            r"([0-9]+\.[0-9]+)/([0-9]+\.[0-9]+)\s+"
            r"([0-9]+\.[0-9]+)/([0-9]+\.[0-9]+)\s+[0-9]+\.[0-9]+%"
        )
        for row in re.finditer(pattern, text):
            name, benchmark, aum, regular_ber, regular_ter, direct_ber, direct_ter = row.groups()
            records.append(
                {
                    "names": {normalize(name)},
                    "page": page,
                    "source_hash": extraction["source_hash"],
                    "layout": "ready_reckoner_v1",
                    "row": {
                        "raw": row.group(),
                        "benchmark_name": benchmark.strip(),
                        "aum": aum,
                        "base_expense_ratio": regular_ber,
                        "regular_ter_not_selected": regular_ter,
                        "direct_ber_not_selected": direct_ber,
                        "direct_ter_not_selected": direct_ter,
                        "as_of": as_of,
                    },
                }
            )
    return records


def ready_fields(detail, as_of):
    # Reuse only unavailable-field construction, never parse other schemes'
    # fields out of the shared table page.
    result = fields("", as_of)
    row = detail["row"]
    if row["as_of"] != as_of:
        return result
    for key, unit in (
        ("benchmark_name", "name"),
        ("aum", "INR_crore"),
        ("base_expense_ratio", "percent"),
    ):
        raw = row[key]
        value = (
            raw
            if key == "benchmark_name"
            else monthly_facts.decimal(
                raw, high=Decimal("10") if key == "base_expense_ratio" else Decimal("1000000000000")
            )
        )
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
            "source_anchor": "Ready Reckoner / " + row["raw"],
        }
    return result


def candidates(rows, identities, extracts):
    text = "\n".join(p["text"] for e in extracts for p in e["pages"])
    headings = {n for e in extracts for p in detail_pages(e) for n in p["names"]}
    # Current names are only a discovery filter. The publication join below
    # still requires original exact identifiers and the actual issuer heading.
    current = {
        normalize(re.split(r"\s+-\s+(?:Regular|Direct)\b", i["scheme_name"], flags=re.I)[0])
        for i in identities
    }
    normalized_lines = {normalize(line) for line in text.splitlines() if line.strip()}
    found = [
        r for r in rows if normalize(r["scheme_name"]) in headings | current | normalized_lines
    ]
    if not 1 <= len(found) <= 200:
        raise ValueError("bounded SBI identity discovery cohort required")
    return sorted(found, key=lambda r: int(r["scheme_id"]))


def fields(text, as_of):
    def missing(reason):
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

    result = {f: missing("explicit_field_anchor_missing_or_ambiguous") for f in FIELDS}

    def accept(key, value, unit, raw, anchor, explicit=False):
        result[key] = {
            "status": "accepted",
            "value": value,
            "unit": unit,
            "scope": "regular_plan" if key == "base_expense_ratio" else "fund",
            "reason": None,
            "disclosure_as_of": as_of,
            "field_effective_date": as_of if explicit else None,
            "date_basis": "explicit_field_date"
            if explicit
            else "document_context_not_field_effective_date",
            "raw_value": raw,
            "source_anchor": anchor,
        }

    day = date.fromisoformat(as_of)
    numeric_date = day.strftime("%d/%m/%Y")
    english_date = day.strftime("%B %d, %Y")
    # Explicit AUM dates or passive header anchor the page's reporting context.
    dated = re.search(r"(?m)^AUM as on\s+" + re.escape(numeric_date) + r"[:\s]", text) or re.search(
        r"All data as of\s+"
        + re.escape(day.strftime("%B"))
        + r"\s+"
        + str(day.day)
        + r",?\s+"
        + str(day.year),
        text,
        re.I,
    )
    if not dated:
        return result
    benchmarks = re.findall(r"(?m)^Benchmark Index\s+([^:\n][^\n]{1,200})\s*\nNAV", text)
    if not benchmarks:
        benchmarks = re.findall(
            r"(?m)^Benchmark Index\s*:\s*\nNAV\s*:\s*\n[0-9]{2}/[0-9]{2}/[0-9]{4}\s*\n([\s\S]{1,250}?)\n(?:Lump sum|Past Performance|Minimum Investment)",
            text,
        )
    if len(benchmarks) == 1:
        value = " ".join(benchmarks[0].split())
        if len(value) <= 250:
            accept("benchmark_name", value, "name", benchmarks[0], "Benchmark Index")
    risks = re.findall(
        r"The risk of the scheme is (Low to Moderate|Moderately High|Very High|Moderate|High|Low)\b",
        text,
        re.I,
    )
    if len(risks) == 1:
        value = {
            v.casefold(): v
            for v in ("Low", "Low to Moderate", "Moderate", "Moderately High", "High", "Very High")
        }[risks[0].casefold()]
        accept("risk_level", value, "risk_category", risks[0], "The risk of the scheme is")
    ratios = re.findall(r"(?m)^TER BER\s*\nRegular:?\s+([0-9]+\.[0-9]+)%\s+([0-9]+\.[0-9]+)%", text)
    ber = [r[1] for r in ratios]
    ber.extend(
        re.findall(r"(?m)^Base Expense Ratio\s*\nRegular\s+([0-9]+\.[0-9]+)%\s+Direct", text)
    )
    if len(ber) == 1:
        accept(
            "base_expense_ratio",
            monthly_facts.decimal(ber[0], high=Decimal("10")),
            "percent",
            ber[0],
            "Regular Base Expense Ratio (BER)",
        )
    amounts = re.findall(
        r"(?m)^AUM as on\s+" + re.escape(numeric_date) + r":\s*`\s*([0-9,]+\.[0-9]+)\s+Cr\b", text
    )
    amounts.extend(
        re.findall(
            r"(?m)^AUM as on\s+"
            + re.escape(day.strftime("%B"))
            + r"\s+"
            + str(day.day)
            + r",\s+"
            + str(day.year)
            + r"\s+`\s*([0-9,]+\.[0-9]+)\s+Crores\b",
            text,
        )
    )
    if len(amounts) == 1:
        accept(
            "aum",
            monthly_facts.decimal(amounts[0], high=Decimal("1000000000000")),
            "INR_crore",
            amounts[0],
            "AUM as on " + (numeric_date if numeric_date in text else english_date),
            True,
        )
    blocks = re.findall(r"Name of Fund Managers\s*\n([\s\S]{1,1000}?)\nTotal Experience", text)
    if not blocks:
        blocks = re.findall(
            r"Name of Fund\s*\nManagers\s*\nTotal\s*\nExperience\s*\nManaging\s*\nSince\s*\n([\s\S]{1,1000}?)(?=\nTER BER|\n\*Mr\.|\n[•]|\nInvestment Objective|\n[0-9]+\. )",
            text,
        )
    if len(blocks) == 1:
        raw = blocks[0]
        names = re.findall(
            r"\b(Mr\.|Ms\.|Mrs\.)\s+([A-Za-z][A-Za-z\s.*()-]{1,120}?)(?=\s+[0-9]+\s+years?\b|\s+Over\s+[0-9]+\s+Years?\b|$)",
            raw,
            re.I,
        )
        display = []
        for title, name in names:
            name = re.sub(r"\*", "", name)
            name = re.sub(r"\(Co-Fund\s+Manager\s*\)", "", name, flags=re.I)
            value = " ".join((title + " " + name).split())
            if not 3 <= len(value) <= 150 or not re.fullmatch(
                r"(?:Mr|Ms|Mrs)\. [A-Za-z]+(?:[ .]+[A-Za-z]+)*\.?", value
            ):
                display = []
                break
            display.append(value)
        # No partially captured manager table is accepted.
        if 1 <= len(display) <= 15 and len(display) == len(re.findall(r"\b(?:Mr|Ms|Mrs)\.", raw)):
            accept("fund_managers", display, "names", raw, "Name of Fund Managers")
    return result


def derive(identity, bridges, details, as_of):
    def gap(why):
        return {
            "status": "gap",
            "reason": why,
            "fields": {
                k: {
                    "status": "unavailable",
                    "value": None,
                    "unit": None,
                    "scope": None,
                    "reason": why,
                    "disclosure_as_of": as_of,
                    "field_effective_date": None,
                    "date_basis": "no_usable_field_evidence",
                    "raw_value": None,
                    "source_anchor": None,
                }
                for k in FIELDS
            },
        }

    if (
        identity["amc"] != AMC
        or identity["plan"] != "REGULAR"
        or identity["option"] not in ("GROWTH", "IDCW")
    ):
        return gap("unsupported_current_identity")
    matches = [r for r in bridges if r["code"] == identity["code"]]
    if not matches:
        return gap("issuer_code_isin_bridge_missing")
    for row in matches:
        label = normalize(row["label"])
        options = {
            v
            for v, words in (("GROWTH", ("growth",)), ("IDCW", ("idcw", "dividend")))
            if any(w in label.split() for w in words)
        }
        if (
            "regular" not in label.split()
            or any(w in label.split() for w in ("direct", "institutional", "retail"))
            or options != {identity["option"]}
        ):
            return gap("issuer_variant_identity_conflict")
    matches = [r for r in matches if r["isin"] in identity["isins"]]
    if not matches:
        return gap("issuer_code_isin_bridge_missing")
    names = set.intersection(*(family_names(r["fund_name"]) for r in matches))
    hits = [d for d in details if names & d["names"]]
    if len(hits) != 1:
        return gap("monthly_heading_missing_or_ambiguous")
    detail = hits[0]
    values = (
        ready_fields(detail, as_of)
        if detail.get("layout") == "ready_reckoner_v1"
        else fields(detail["page"]["text"], as_of)
    )
    for value in values.values():
        value.update(
            content_hash=detail["source_hash"],
            source_url=detail.get("source_url"),
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
