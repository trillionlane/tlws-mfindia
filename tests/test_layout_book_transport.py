"""Provider-free source-boundary probes; no DNS or cloud calls."""

import socket
from types import SimpleNamespace

import pytest

from mfdataindia.enrichment import kotak_acquisition as acquisition, layout_book_http as transport


@pytest.mark.parametrize(
    "url",
    [
        "http://www.amfiindia.com/api/schemes/4372/documents",
        "https://www.amfiindia.com.evil.invalid/api/schemes/4372/documents",
        "https://user@www.amfiindia.com/api/schemes/4372/documents",
        "https://www.amfiindia.com/api/schemes/4372/documents?cookie=1",
        "https://vatseelabs-s3.kotakmf.com/private.pdf",
    ],
)
def test_unregistered_endpoints_rejected_before_dns(monkeypatch, url):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: pytest.fail("no DNS permitted"))
    with pytest.raises(ValueError):
        acquisition.fetch(url)


def test_every_dns_answer_must_be_public_before_connect(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (None, None, None, None, ("1.1.1.1", 443)),
            (None, None, None, None, ("127.0.0.1", 443)),
        ],
    )
    monkeypatch.setattr(
        transport, "PinnedHTTPS", lambda *a: pytest.fail("private DNS must not connect")
    )
    with pytest.raises(ValueError, match="every source DNS answer"):
        acquisition.fetch("https://www.amfiindia.com/api/schemes/4372/documents")


@pytest.mark.parametrize("defect", [None, "redirect", "encoding", "media", "truncated", "limit"])
def test_pinned_public_get_no_retry_redirect_proxy_or_cookie(monkeypatch, defect):
    calls = []

    class Response:
        status = 302 if defect == "redirect" else 200

        def __init__(self):
            self.raw = bytearray(b'{"data":[]}')

        def getheader(self, name, default=None):
            return {
                "Content-Type": "text/html" if defect == "media" else "application/json",
                "Content-Encoding": "gzip" if defect == "encoding" else "identity",
                "Content-Length": "100000000"
                if defect == "limit"
                else ("99" if defect == "truncated" else str(len(b'{"data":[]}'))),
            }.get(name, default)

        def read1(self, maximum):
            data = bytes(self.raw[:maximum])
            del self.raw[:maximum]
            return data

    class Connection:
        sock = SimpleNamespace(settimeout=lambda *a: None)

        def __init__(self, *a):
            calls.append(("connect", a))

        def request(self, *a, **k):
            calls.append(("request", a, k))

        def getresponse(self):
            return Response()

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("1.1.1.1", 443))]
    )
    monkeypatch.setattr(transport, "PinnedHTTPS", Connection)
    if defect:
        with pytest.raises(ValueError):
            acquisition.fetch("https://www.amfiindia.com/api/schemes/4372/documents")
    else:
        assert (
            acquisition.fetch("https://www.amfiindia.com/api/schemes/4372/documents")["content"]
            == b'{"data":[]}'
        )
    assert len([c for c in calls if c[0] == "connect"]) == 1
    request = next(c for c in calls if c[0] == "request")
    assert request[1] == ("GET", "/api/schemes/4372/documents")
    assert set(request[2]["headers"]) == {"Accept", "Accept-Encoding", "User-Agent"}
    assert calls[-1] == ("close",)
