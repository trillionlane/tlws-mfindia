"""Actual public forms catalogue plus one reviewed historical issuer link."""

import calendar
import json
import re
from datetime import date

from mfdataindia.enrichment import kotak_book as book, layout_book_http

CATALOGUE_PATTERN = re.compile(
    re.escape(book.CATALOGUE) + r"\?year=(20[0-9]{2})&month=([1-9]|1[0-2])&type=month"
)
BOOK_PATTERN = re.compile(
    re.escape(book.BOOK_PREFIX)
    + r"Factsheet-for-([A-Za-z]+)-(20[0-9]{2})/KotakMFFactsheet([A-Za-z]+)(20[0-9]{2})\.pdf"
)


def classify(url):
    if CATALOGUE_PATTERN.fullmatch(url):
        return "catalogue", 1048576, ("application/json",)
    if url == book.DIRECTORY:
        return "directory", 1048576, ("application/json",)
    if BOOK_PATTERN.fullmatch(url) or url == book.HISTORICAL:
        return "book", 32 * 1024 * 1024, ("application/pdf",)
    if re.fullmatch(r"https://www\.amfiindia\.com/api/schemes/[1-9][0-9]{0,7}/documents", url):
        return "index", 1048576, ("application/json",)
    if re.fullmatch(r"https://portal\.amfiindia\.com/spages/SSD_[1-9][0-9]{0,7}\.xml", url):
        return "identity", 1048576, ("text/xml", "application/xml")
    raise ValueError("unregistered Kotak/AMFI endpoint")


def fetch(url):
    return layout_book_http.fetch(url, classify)


def recent_catalogues(observed):
    year, month = observed.year, observed.month
    result = []
    for _ in range(2):
        month -= 1
        if month == 0:
            month, year = 12, year - 1
        result.append(
            {
                "expected_period": date(
                    year, month, calendar.monthrange(year, month)[1]
                ).isoformat(),
                "source_url": book.CATALOGUE + f"?year={year}&month={month}&type=month",
            }
        )
    return result


def catalogue_book(content, catalogue_url):
    match = CATALOGUE_PATTERN.fullmatch(catalogue_url)
    if not match or not isinstance(content, bytes) or not 0 < len(content) <= 1048576:
        raise ValueError("bounded actual monthly catalogue required")
    doc = json.loads(content)
    if not isinstance(doc, dict):
        raise ValueError("actual monthly catalogue object required")
    rows = doc.get("subHeaderList") if isinstance(doc, dict) else None
    if (
        doc.get("status") != "Success"
        or str(doc.get("statusCode")) != "200"
        or not isinstance(rows, list)
        or len(rows) != 1
    ):
        raise ValueError("one successful actual monthly factsheet entry required")
    row = rows[0]
    path = row.get("content")
    if (
        not isinstance(path, str)
        or row.get("headerId") != 219
        or row.get("contentType") != "upload"
    ):
        raise ValueError("actual factsheet upload entry required")
    url = "https://vatseelabs-s3.kotakmf.com/" + path
    pdf = BOOK_PATTERN.fullmatch(url)
    if (
        not pdf
        or pdf[1] != pdf[3]
        or pdf[2] != pdf[4]
        or int(pdf[2]) != int(match[1])
        or pdf[1].casefold() != calendar.month_name[int(match[2])].casefold()
    ):
        raise ValueError("issuer-returned monthly book path differs")
    classify(url)
    return url
