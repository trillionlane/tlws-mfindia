"""Hermetic hostile transport bounds; no live source/network calls."""

from types import SimpleNamespace
import pytest
from mfdataindia.enrichment import refresh_acquisition as acquisition

URL = "https://mutualfund.adityabirlacapital.com/-/media/bsl/files/resources/factsheets/2026/month.pdf"


@pytest.mark.parametrize(
    "fault",
    ["none", "redirect", "compressed", "wrong_media", "large", "short", "private_dns", "not_pdf"],
)
def test_book_fetch_pins_public_dns_and_rejects_unsafe_or_incomplete_transport(monkeypatch, fault):
    raw = b"%PDF-fixture" if fault != "not_pdf" else b"HTML not PDF"
    address = "127.0.0.1" if fault == "private_dns" else "8.8.8.8"
    monkeypatch.setattr(
        acquisition.socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", (address, 443))]
    )
    calls = []

    class Connection:
        def __init__(self, host, pinned, timeout):
            assert pinned == "8.8.8.8" and timeout == 120
            self.sock = SimpleNamespace(settimeout=lambda value: None)
            self.chunks = [raw, b""]

        def request(self, method, path, headers):
            calls.append((method, path, headers))
            assert headers["Accept-Encoding"] == "identity"

        def getresponse(self):
            headers = {
                "Content-Type": "text/html" if fault == "wrong_media" else "application/pdf",
                "Content-Encoding": "gzip" if fault == "compressed" else "identity",
                "Content-Length": str(16 * 1024 * 1024 + 1)
                if fault == "large"
                else str(len(raw) + 1 if fault == "short" else len(raw)),
            }
            return SimpleNamespace(
                status=302 if fault == "redirect" else 200,
                getheader=lambda name, default=None: headers.get(name, default),
                read1=lambda size: self.chunks.pop(0),
            )

        def close(self):
            pass

    monkeypatch.setattr(acquisition, "PinnedHTTPS", Connection)
    if fault == "none":
        assert acquisition.fetch(URL, book=True)["content"] == raw
    else:
        with pytest.raises(ValueError):
            acquisition.fetch(URL, book=True)
    assert len(calls) == (0 if fault == "private_dns" else 1)
