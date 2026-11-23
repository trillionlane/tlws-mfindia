"""SBI dual-date evidence without changing the frozen v1 parser contract."""

import calendar
import re
from copy import deepcopy
from datetime import date

from mfdataindia.enrichment import sbi_book as legacy

AMC = legacy.AMC
KEY = legacy.KEY
CATALOGUE = legacy.CATALOGUE
DIRECTORY = legacy.DIRECTORY
FIELDS = legacy.FIELDS
BOOK = legacy.BOOK
MONTHS = legacy.MONTHS

editions = legacy.editions
directory = legacy.directory
identity_rows = legacy.identity_rows
detail_pages = legacy.detail_pages
candidates = legacy.candidates

FACTSHEET_DATE = re.compile(
    r"\bfactsheet\s+as\s+on\s+([A-Za-z]+)\s+([0-9]{1,2})"
    r"(?:st|nd|rd|th)?\s*,?\s*(20[0-9]{2})\b",
    re.IGNORECASE,
)


def policy():
    result = deepcopy(legacy.policy())
    result.update(
        version=2,
        books=(
            "one AMC batch per catalogue reporting period, combining distinct active/passive "
            "originals; reporting_period remains calendar month-end"
        ),
        dates=(
            "catalogue reporting_period is the stable month-end publication key; document_as_of "
            "is the unique exact first-page PDF date; explicit field dates equal document_as_of"
        ),
    )
    return result


def document_date_evidence(extraction, reporting_period):
    """Return exact first-page source date evidence within one month-end period."""
    if (
        not isinstance(extraction, dict)
        or not isinstance(extraction.get("pages"), list)
        or not isinstance(reporting_period, str)
    ):
        raise ValueError("SBI document date evidence required")
    try:
        period = date.fromisoformat(reporting_period)
    except ValueError:
        raise ValueError("SBI reporting period invalid") from None
    if period.day != calendar.monthrange(period.year, period.month)[1]:
        raise ValueError("SBI reporting period must be month-end")
    covers = [page for page in extraction["pages"] if page.get("page") == 1]
    if len(covers) != 1 or not isinstance(covers[0].get("text"), str):
        raise ValueError("unique SBI document cover required")
    matches = []
    for found in FACTSHEET_DATE.finditer(covers[0]["text"]):
        month, day, year = found.groups()
        number = MONTHS.get(month.casefold())
        if number is None:
            raise ValueError("SBI document date month invalid")
        try:
            resolved = date(int(year), number, int(day))
        except ValueError:
            raise ValueError("SBI document date invalid") from None
        matches.append((resolved, " ".join(found.group().split())))
    unique = {resolved for resolved, _ in matches}
    if len(unique) != 1:
        raise ValueError("unique SBI document factsheet date required")
    resolved = unique.pop()
    if (resolved.year, resolved.month) != (period.year, period.month):
        raise ValueError("SBI document date outside reporting month")
    anchors = sorted({anchor for value, anchor in matches if value == resolved})
    return {
        "document_as_of": resolved.isoformat(),
        "page": 1,
        "text_hash": covers[0]["text_hash"],
        "source_anchor": anchors[0],
    }


def paired_document_dates(extractions, reporting_period):
    if not isinstance(extractions, dict) or set(extractions) != {"active", "passive"}:
        raise ValueError("SBI active/passive document date evidence required")
    evidence = {
        role: document_date_evidence(extraction, reporting_period)
        for role, extraction in extractions.items()
    }
    if len({item["document_as_of"] for item in evidence.values()}) != 1:
        raise ValueError("SBI active/passive document dates differ")
    return evidence


def derive(identity, bridges, details, reporting_period, document_as_of):
    """Derive against the source date, then preserve month-end publication identity."""
    period = date.fromisoformat(reporting_period)
    source_day = date.fromisoformat(document_as_of)
    if period.day != calendar.monthrange(period.year, period.month)[1] or (
        source_day.year,
        source_day.month,
    ) != (period.year, period.month):
        raise ValueError("SBI reporting period/document date pair invalid")
    result = legacy.derive(identity, bridges, details, document_as_of)
    for field in result["fields"].values():
        if field["date_basis"] == "explicit_field_date":
            if field["field_effective_date"] != document_as_of:
                raise ValueError("SBI explicit field date differs from document date")
        elif field["field_effective_date"] is not None:
            raise ValueError("SBI context-only field cannot acquire an effective date")
        field["reporting_period"] = reporting_period
        field["disclosure_as_of"] = reporting_period
        field["document_as_of"] = document_as_of
    return result
