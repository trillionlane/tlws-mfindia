"""Exact production UTI English catalogues and issuer-linked public originals."""

import calendar
import json
import re
from datetime import date

from mfdataindia.enrichment import layout_book_http, uti_book as book

MONTH_PATTERN = "|".join(calendar.month_name[1:])
CATALOGUE_PATTERN = re.compile(
    re.escape(book.CATALOGUE) + r"\?year=(20[0-9]{2})&month=(" + MONTH_PATTERN + r")"
)
CURRENT_PATTERN = re.compile(
    re.escape(book.CDN)
    + r"utimf-job/ebook/UTI%20Fund%20Watch%20\((Active|Passive)\)-("
    + MONTH_PATTERN
    + r")%20(20[0-9]{2})\.pdf"
)
OLDER_PATTERN = re.compile(
    re.escape(book.CDN)
    + r"s3fs-public/(20[0-9]{2})-(0[1-9]|1[0-2])/uti_fund_watch_(active|passive)_("
    + MONTH_PATTERN.lower()
    + r")_(20[0-9]{2})(?:_rv[1-9][0-9]?)?\.pdf\?VersionId=[A-Za-z0-9._-]{1,200}"
)


def classify(url):
    if CATALOGUE_PATTERN.fullmatch(url):
        return "catalogue", 1048576, ("application/json",)
    if url == book.DIRECTORY:
        return "directory", 1048576, ("application/json",)
    if CURRENT_PATTERN.fullmatch(url) or OLDER_PATTERN.fullmatch(url):
        return "book", 16 * 1024 * 1024, ("application/pdf",)
    if re.fullmatch(r"https://www\.amfiindia\.com/api/schemes/[1-9][0-9]{0,7}/documents", url):
        return "index", 1048576, ("application/json",)
    if re.fullmatch(r"https://portal\.amfiindia\.com/spages/SSD_[1-9][0-9]{0,7}\.xml", url):
        return "identity", 1048576, ("application/xml", "text/xml")
    raise ValueError("unregistered production UTI/AMFI endpoint")


def fetch(url):
    return layout_book_http.fetch(url, classify)


def requested_url(raw, *, kind, month, year):
    if (
        not isinstance(raw, str)
        or not 1 <= len(raw) <= 2048
        or any(ord(c) < 32 or ord(c) > 126 for c in raw)
    ):
        raise ValueError("bounded plain issuer public URL required")
    # Only literal filename spaces in the two reviewed public layouts are
    # normalized. No general decoding, signed URL rewriting or host fallback.
    url = raw.replace(" ", "%20")
    current = CURRENT_PATTERN.fullmatch(url)
    older = OLDER_PATTERN.fullmatch(url)
    if current:
        if (current[1], current[2], current[3]) != (kind, month, str(year)):
            raise ValueError("English issuer row/path period or active/passive kind differs")
    elif older:
        if (older[1], older[2], older[3], older[4], older[5]) != (
            str(year),
            f"{list(calendar.month_name).index(month):02}",
            kind.lower(),
            month.lower(),
            str(year),
        ):
            raise ValueError("versioned English issuer path differs")
    else:
        raise ValueError("only reviewed issuer-linked English CDN layouts permitted")
    return url


def catalogue_books(content, url):
    match = CATALOGUE_PATTERN.fullmatch(url)
    if not match or not isinstance(content, bytes) or not 0 < len(content) <= 1048576:
        raise ValueError("bounded actual English catalogue required")
    document = json.loads(content)
    if (
        not isinstance(document, dict)
        or not isinstance(document.get("rows"), list)
        or not 2 <= len(document["rows"]) <= 64
    ):
        raise ValueError("bounded issuer catalogue rows required")
    year, month = match[1], match[2]
    result = []
    for kind in ("Active", "Passive"):
        name = "UTI Fund Watch (" + kind + ")-" + month + " " + year
        matches = [r for r in document["rows"] if isinstance(r, dict) and r.get("name") == name]
        if len(matches) != 1:
            raise ValueError("one exact English " + kind + " row required; no Hindi fallback")
        row = matches[0]
        if (
            row.get("doc") != row.get("url")
            or row.get("type") != "pdf"
            or row.get("category") != "Fact Sheet"
            or row.get("month") != month
            or row.get("year") != year
        ):
            raise ValueError("exact English PDF catalogue metadata differs")
        result.append(
            {
                "kind": kind.lower(),
                "language": "English",
                "issuer_url": row["url"],
                "source_url": requested_url(row["url"], kind=kind, month=month, year=year),
                "catalogue_url": url,
            }
        )
    return result


def recent_catalogues(observed):
    result = []
    year, month = observed.year, observed.month
    for _ in range(2):
        prior_month = month - 1
        prior_year = year
        if prior_month == 0:
            prior_year -= 1
            prior_month = 12
        result.append(
            {
                "source_url": book.CATALOGUE + f"?year={year}&month=" + calendar.month_name[month],
                "expected_period": date(
                    prior_year, prior_month, calendar.monthrange(prior_year, prior_month)[1]
                ).isoformat(),
            }
        )
        year, month = prior_year, prior_month
    return result
