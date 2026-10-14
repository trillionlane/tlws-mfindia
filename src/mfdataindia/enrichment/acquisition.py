"""Operator-only bounded HTTPS acquisition with DNS pinning and no proxies/cookies."""

import http.client
import ipaddress
import socket
import ssl
import time
from contextlib import closing
from urllib.parse import urljoin, urlsplit, unquote

from requests.certs import where as public_ca_bundle

from mfdataindia.enrichment.evidence import MAX_BYTES, public_url


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(
            host, timeout=timeout, context=ssl.create_default_context(cafile=public_ca_bundle())
        )
        self.address = address

    def connect(self):
        # Resolve once externally, check EVERY answer, connect only to that
        # approved numeric address. TLS verifies the original hostname/SNI.
        sock = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def fetch(url, official_hosts, *, timeout=20, maximum=MAX_BYTES):
    return _fetch(url, official_hosts, timeout=timeout, maximum=maximum)


def fetch_absl_monthly(html, year):
    """One constructed public Sitecore query; no general query/redirect exception."""
    from mfdataindia.ingest.amc_factsheets.download import monthly_api_url

    url = monthly_api_url(html, year)

    def validate(value, hosts):
        if value != url:
            raise ValueError("Sitecore API redirect not permitted")
        public_url(value.split("?", 1)[0], hosts)
        return value

    return _fetch(
        url,
        ["mutualfund.adityabirlacapital.com"],
        maximum=1024 * 1024,
        validate=validate,
        media_types=("application/json",),
    )


def fetch_absl_document(url):
    """Official media uses a fixed, non-secret same-origin Referer (legacy behavior)."""
    from mfdataindia.ingest.amc_factsheets.download import BASE, _LANDING, factsheet_media_url

    hosts = ["mutualfund.adityabirlacapital.com"]
    def validate(value, allowed):
        public_url(value, allowed)
        return factsheet_media_url(value)

    return _fetch(url, hosts, timeout=30, referer=BASE + _LANDING,
                  media_types=("application/pdf",), validate=validate)


def _fetch(
    url, official_hosts, *, timeout=20, maximum=MAX_BYTES,
    validate=public_url, media_types=("text/html", "application/pdf"),
    referer=None,
):
    if not 0 < maximum <= MAX_BYTES or not 0 < timeout <= 30:
        raise ValueError("bounded acquisition limits required")
    hosts = set(official_hosts)
    validate(url, hosts)
    start, chain = time.monotonic(), []
    for _ in range(4):
        validate(url, hosts)
        parsed = urlsplit(url)
        records = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        addresses = sorted({record[4][0] for record in records})
        if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
            raise ValueError("non-public official-source DNS answer")
        remaining = timeout - (time.monotonic() - start)
        if remaining <= 0:
            raise TimeoutError("official-source acquisition deadline")
        with closing(PinnedHTTPS(parsed.hostname, addresses[0], remaining)) as conn:
            conn.request(
                "GET",
                parsed.path + ("?" + parsed.query if parsed.query else ""),
                headers={
                    "User-Agent": "MFDataIndia-Evidence/1.0",
                    "Accept-Encoding": "identity",
                    "Accept": ",".join(media_types),
                    **({"Referer": referer} if referer else {}),
                },
            )
            response = conn.getresponse()
            chain.append(url)
            if response.status in (301, 302, 303, 307, 308):
                destination = response.getheader("Location")
                if not destination:
                    raise ValueError("redirect without destination")
                if any(part in (".", "..") for part in unquote(destination).split("/")):
                    raise ValueError("redirect path traversal not permitted")
                url = validate(urljoin(url, destination), hosts)
                continue
            if response.status != 200:
                raise ValueError("official-source HTTP acquisition failed")
            if response.getheader("Content-Encoding", "identity") != "identity":
                raise ValueError("compressed document transport unsupported")
            declared = response.getheader("Content-Length")
            if declared and (not declared.isdigit() or not 0 < int(declared) <= maximum):
                raise ValueError("declared document size exceeds bound")
            media = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
            if media not in media_types:
                raise ValueError("unsupported document media type")
            data = bytearray()
            while True:
                remaining = timeout - (time.monotonic() - start)
                if remaining <= 0:
                    raise TimeoutError("official-source acquisition deadline")
                if conn.sock:
                    conn.sock.settimeout(remaining)
                chunk = response.read1(min(65536, maximum + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > maximum:
                    raise ValueError("streamed document exceeds bound")
            if not data or (declared and len(data) != int(declared)):
                raise ValueError("empty or incomplete official document")
            return {
                "content": bytes(data),
                "media_type": media,
                "final_url": url,
                "redirect_chain": chain,
            }
    raise ValueError("official-source redirect limit exceeded")
