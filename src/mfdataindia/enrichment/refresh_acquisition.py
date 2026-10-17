"""Fixed official catalog discovery and bounded, pinned monthly-book acquisition."""

import hashlib
import ipaddress
import json
import re
import socket
import time
from contextlib import closing
from datetime import datetime
from urllib.parse import urlsplit

from mfdataindia.enrichment.acquisition import PinnedHTTPS
from mfdataindia.enrichment.evidence import public_url
from mfdataindia.enrichment import monthly_pdf
from mfdataindia.enrichment.monthly_facts import NAME
from mfdataindia.ingest.amc_factsheets.download import (
    BASE,
    _LANDING,
    _MONTH_ORDER,
    monthly_api_url,
    monthly_documents,
    factsheet_media_url,
)

HOSTS = {"mutualfund.adityabirlacapital.com"}


def fetch(url, *, book=False, catalog_api=False):
    # Query exception only for the constructed Sitecore catalog, never redirects.
    public_url(url.split("?", 1)[0] if catalog_api else url, HOSTS)
    if book:
        factsheet_media_url(url)
    elif catalog_api:
        parsed = urlsplit(url)
        if parsed.path != "/api/sitecore/CalculatorPage/GetMonthlyFactsheetsByYear":
            raise ValueError("fixed catalog API required")
    elif url != BASE + _LANDING:
        raise ValueError("fixed official landing required")
    parsed = urlsplit(url)
    addresses = sorted(
        {r[4][0] for r in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)},
        key=lambda address: (
            ipaddress.ip_address(address).version,
            ipaddress.ip_address(address).packed,
        ),
    )
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise ValueError("official source DNS not public")
    limit, deadline = (16 * 1024 * 1024, 120) if book else (1024 * 1024, 30)
    started = time.monotonic()
    with closing(PinnedHTTPS(parsed.hostname, addresses[0], deadline)) as conn:
        conn.request(
            "GET",
            parsed.path + ("?" + parsed.query if parsed.query else ""),
            headers={
                "Accept-Encoding": "identity",
                "Accept": "application/pdf"
                if book
                else "application/json"
                if catalog_api
                else "text/html",
                "Referer": BASE + _LANDING,
                "User-Agent": "MFDataIndia-OfficialFacts/1.0",
            },
        )
        response = conn.getresponse()
        media = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
        expected = "application/pdf" if book else "application/json" if catalog_api else "text/html"
        declared = response.getheader("Content-Length")
        if (
            response.status != 200
            or media != expected
            or response.getheader("Content-Encoding", "identity") != "identity"
            or declared
            and (not declared.isdigit() or not 0 < int(declared) <= limit)
        ):
            raise ValueError("official source status, media or byte bound differs")
        data = bytearray()
        while True:
            remaining = deadline - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("official source deadline exceeded")
            if conn.sock:
                conn.sock.settimeout(remaining)
            chunk = response.read1(min(65536, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > limit:
                raise ValueError("official source byte bound exceeded")
        if not data or declared and len(data) != int(declared):
            raise ValueError("official source incomplete")
        if book and not data.startswith(b"%PDF-"):
            raise ValueError("official book is not PDF")
        return {
            "content": bytes(data),
            "source_url": url,
            "media_type": media,
            "content_hash": hashlib.sha256(data).hexdigest(),
        }


def discover(now, *, transport=fetch):
    landing = transport(BASE + _LANDING)
    html = landing["content"].decode("utf-8")
    catalogs = [landing]
    for year in (now.year, now.year - 1):
        catalog = transport(monthly_api_url(html, year), catalog_api=True)
        catalogs.append(catalog)
        documents = monthly_documents(json.loads(catalog["content"]), year)
        if documents:
            ordered = sorted(documents, key=lambda d: _MONTH_ORDER[d.period.split()[0]])
            document = ordered[-1]
            month = _MONTH_ORDER[document.period.split()[0]]
            if (year, month) > (now.year, now.month):
                raise ValueError("future publication month is not usable")
            return catalogs, document
    raise ValueError("official monthly catalog has no document")


def pages(content):
    result = monthly_pdf.extract(content, start=1, end=24)
    matches = []
    for page in result["pages"][:-1]:
        text = page["text"]
        if (
            re.search(r"(?m)^\s*" + re.escape(NAME) + r"\s*$", text, re.I)
            and "Fund Details" in text
        ):
            dates = re.findall(r"NAV\s+as\s+(?:on|of)\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})", text)
            if len(set(dates)) == 1:
                day = datetime.strptime(dates[0], "%B %d, %Y").date()
                adjacent = result["pages"][page["page"]]["text"]
                if NAME.lower() in adjacent.lower():
                    matches.append((page["page"], page["page"] + 1, day))
    if len(matches) != 1:
        raise ValueError("monthly fund detail/changes page discovery is ambiguous")
    return matches[0]
