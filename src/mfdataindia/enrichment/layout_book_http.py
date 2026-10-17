"""Bounded ordinary HTTPS for new reviewed layout adapters; no redirects/retry."""

import ipaddress
import socket
import time
from contextlib import closing
from urllib.parse import urlsplit

from mfdataindia.enrichment.acquisition import PinnedHTTPS


def fetch(url, classify):
    if (
        not isinstance(url, str)
        or not 0 < len(url) <= 2048
        or any(ord(c) <= 32 or ord(c) >= 127 for c in url)
    ):
        raise ValueError("bounded ASCII public source URL required")
    kind, maximum, media_types = classify(url)
    if not 0 < maximum <= 32 * 1024 * 1024:
        raise ValueError("reviewed bounded source limit required")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.port
    ):
        raise ValueError("plain official HTTPS endpoint required")
    started = time.monotonic()
    addresses = sorted(
        {r[4][0] for r in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)},
        key=lambda a: (ipaddress.ip_address(a).version, ipaddress.ip_address(a).packed),
    )
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise ValueError("every source DNS answer must be public")
    remaining = 30 - (time.monotonic() - started)
    if remaining <= 0:
        raise TimeoutError("official-source deadline")
    with closing(PinnedHTTPS(parsed.hostname, addresses[0], remaining)) as conn:
        conn.request(
            "GET",
            parsed.path + ("?" + parsed.query if parsed.query else ""),
            headers={
                "Accept": ",".join(media_types),
                "Accept-Encoding": "identity",
                "User-Agent": "MFDataIndia-OfficialFacts/1.0",
            },
        )
        response = conn.getresponse()
        if (
            response.status != 200
            or response.getheader("Content-Encoding", "identity") != "identity"
        ):
            raise ValueError(
                "ordinary source unavailable; no redirects, challenge or automatic retry"
            )
        media = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
        declared = response.getheader("Content-Length")
        if media not in media_types or (
            declared and (not declared.isdigit() or not 0 < int(declared) <= maximum)
        ):
            raise ValueError("source media/declared byte limit differs")
        raw = bytearray()
        while True:
            remaining = 30 - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("official-source deadline")
            if conn.sock:
                conn.sock.settimeout(remaining)
            chunk = response.read1(min(65536, maximum + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > maximum:
                raise ValueError("source streamed byte limit exceeded")
        if (
            not raw
            or (declared and len(raw) != int(declared))
            or (kind == "book" and not raw.startswith(b"%PDF-"))
        ):
            raise ValueError("incomplete original or non-PDF book")
    return {
        "source_url": url,
        "final_url": url,
        "content": bytes(raw),
        "media_type": "application/xml" if kind == "identity" else media,
        "role": kind,
    }
