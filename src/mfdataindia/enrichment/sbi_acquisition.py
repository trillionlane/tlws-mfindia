"""The SBI/AMFI public browser flow, bounded and without access-control bypass."""

import ipaddress
import re
import socket
import time
from contextlib import closing
from urllib.parse import urlsplit

from mfdataindia.enrichment.acquisition import PinnedHTTPS
from mfdataindia.enrichment.sbi_book import BOOK, CATALOGUE, DIRECTORY


def classify(url):
    if (
        not isinstance(url, str)
        or len(url) > 2048
        or any(ord(c) <= 32 or ord(c) >= 127 for c in url)
    ):
        raise ValueError("bounded public SBI/AMFI URL required")
    if url == CATALOGUE:
        return "catalogue", "POST", 1048576, ("text/html",)
    if url == DIRECTORY:
        return "directory", "GET", 1048576, ("application/json",)
    if BOOK.fullmatch(url):
        return "book", "GET", 16 * 1024 * 1024, ("application/pdf",)
    if re.fullmatch(r"https://www\.amfiindia\.com/api/schemes/[1-9][0-9]{0,7}/documents", url):
        return "index", "GET", 1048576, ("application/json",)
    if re.fullmatch(r"https://portal\.amfiindia\.com/spages/SSD_[1-9][0-9]{0,7}\.xml", url):
        return "identity", "GET", 1048576, ("text/xml", "application/xml")
    raise ValueError("unregistered SBI/AMFI endpoint")


def fetch(url):
    kind, method, maximum, media_types = classify(url)
    parsed = urlsplit(url)
    started = time.monotonic()
    addresses = sorted(
        {r[4][0] for r in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)},
        key=lambda a: (ipaddress.ip_address(a).version, ipaddress.ip_address(a).packed),
    )
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise ValueError("non-public official-source DNS answer")
    remaining = 30 - (time.monotonic() - started)
    if remaining <= 0:
        raise TimeoutError("official-source acquisition deadline")
    with closing(PinnedHTTPS(parsed.hostname, addresses[0], remaining)) as conn:
        conn.request(
            method,
            parsed.path + ("?" + parsed.query if parsed.query else ""),
            body=b"" if method == "POST" else None,
            headers={
                "Accept": ",".join(media_types),
                "Accept-Encoding": "identity",
                "User-Agent": "MFDataIndia-OfficialFacts/1.0",
                **(
                    {
                        "Content-Type": "application/json;charset=utf-8",
                        "Origin": "https://www.sbimf.com",
                        "Referer": "https://www.sbimf.com/factsheets",
                    }
                    if method == "POST"
                    else {}
                ),
            },
        )
        response = conn.getresponse()
        # No redirects, cookies, challenge responses, proxy or address rotation.
        if (
            response.status != 200
            or response.getheader("Content-Encoding", "identity") != "identity"
        ):
            raise ValueError("official-source HTTP acquisition failed")
        reported_media = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
        declared = response.getheader("Content-Length")
        if reported_media not in media_types or (
            declared and (not declared.isdigit() or not 0 < int(declared) <= maximum)
        ):
            raise ValueError("official-source media/byte bound differs")
        raw = bytearray()
        while True:
            remaining = 30 - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("official-source acquisition deadline")
            if conn.sock:
                conn.sock.settimeout(remaining)
            chunk = response.read1(min(65536, maximum + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > maximum:
                raise ValueError("official-source byte bound exceeded")
        if not raw or (declared and len(raw) != int(declared)):
            raise ValueError("incomplete official source")
    if kind == "book" and not raw.startswith(b"%PDF-"):
        raise ValueError("official book is not a PDF")
    return {
        "source_url": url,
        "final_url": url,
        "content": bytes(raw),
        "media_type": "application/xml" if kind == "identity" else reported_media,
        "reported_media_type": reported_media,
        "role": kind,
    }


def identity_url(content, sid):
    """Accept only the exact XML URL actually returned by this scheme index."""
    import json

    doc = json.loads(content)
    rows = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(rows, list) or len(rows) != 1 or str(rows[0].get("schemeId")) != sid:
        raise ValueError("AMFI document-index scheme identity differs")
    url = rows[0].get("summaryXmlUrl")
    if url != "https://portal.amfiindia.com/spages/SSD_" + sid + ".xml":
        raise ValueError("AMFI returned XML URL unavailable or unregistered")
    classify(url)
    return url
