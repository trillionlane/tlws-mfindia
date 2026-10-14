"""Operator-only bounded HTTPS acquisition with DNS pinning and no proxies/cookies."""

import http.client
import ipaddress
import socket
import ssl
import time
from contextlib import closing
from urllib.parse import urljoin, urlsplit

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
    if not 0 < maximum <= MAX_BYTES or not 0 < timeout <= 30:
        raise ValueError("bounded acquisition limits required")
    hosts = set(official_hosts)
    public_url(url, hosts)
    start, chain = time.monotonic(), []
    for _ in range(4):
        public_url(url, hosts)
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
                parsed.path,
                headers={
                    "User-Agent": "MFDataIndia-Evidence/1.0",
                    "Accept-Encoding": "identity",
                    "Accept": "text/html,application/pdf",
                },
            )
            response = conn.getresponse()
            chain.append(url)
            if response.status in (301, 302, 303, 307, 308):
                destination = response.getheader("Location")
                if not destination:
                    raise ValueError("redirect without destination")
                url = public_url(urljoin(url, destination), hosts)
                continue
            if response.status != 200:
                raise ValueError("official-source HTTP acquisition failed")
            if response.getheader("Content-Encoding", "identity") != "identity":
                raise ValueError("compressed document transport unsupported")
            declared = response.getheader("Content-Length")
            if declared and (not declared.isdigit() or not 0 < int(declared) <= maximum):
                raise ValueError("declared document size exceeds bound")
            media = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
            if media not in ("text/html", "application/pdf"):
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
