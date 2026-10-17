"""Hermetic HTTPS boundary tests; no public or private network calls."""

import pytest

from mfdataindia.enrichment import sbi_acquisition as acquisition
from mfdataindia.enrichment.sbi_book import CATALOGUE


def dns(*addresses):
    return [(None, None, None, None, (a, 443)) for a in addresses]


class Response:
    status = 200
    headers = {"Content-Type": "text/html", "Content-Encoding": "identity", "Content-Length": "2"}
    content = b"ok"

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read1(self, size):
        data = self.content[:size]
        self.content = self.content[size:]
        return data


def boundary(monkeypatch, response=None):
    events = []

    class Connection:
        sock = None

        def __init__(self, host, address, timeout):
            events.append(("connect", host, address, timeout))

        def request(self, *args, **kwargs):
            events.append(("request", args, kwargs))

        def getresponse(self):
            return response or Response()

        def close(self):
            events.append(("close",))

    monkeypatch.setattr(acquisition, "PinnedHTTPS", Connection)
    return events


def test_numeric_public_address_pinning_prefers_ipv4_without_rotation(monkeypatch):
    monkeypatch.setattr(
        acquisition.socket, "getaddrinfo", lambda *a, **k: dns("2606:4700:4700::1111", "8.8.8.8")
    )
    events = boundary(monkeypatch)
    result = acquisition.fetch(CATALOGUE)
    assert result["content"] == b"ok" and result["source_url"] == CATALOGUE
    assert events[0][:3] == ("connect", "www.sbimf.com", "8.8.8.8")
    method, path = events[1][1]
    assert method == "POST" and path == "/ajaxcall/CMS/GetRecentFactSheets"
    assert events[1][2]["body"] == b""
    headers = events[1][2]["headers"]
    assert headers["Accept-Encoding"] == "identity"
    assert not ({"Authorization", "Cookie", "Proxy-Authorization"} & set(headers))
    assert len([e for e in events if e[0] == "connect"]) == 1


def test_any_private_dns_answer_fails_before_connect_even_with_public_answer(monkeypatch):
    monkeypatch.setattr(
        acquisition.socket, "getaddrinfo", lambda *a, **k: dns("8.8.8.8", "169.254.169.254")
    )
    events = boundary(monkeypatch)
    with pytest.raises(ValueError, match="non-public"):
        acquisition.fetch(CATALOGUE)
    assert events == []


@pytest.mark.parametrize("defect", ["redirect", "compressed", "oversize", "media", "incomplete"])
def test_challenges_compression_bad_media_and_bounds_are_not_bypassed(monkeypatch, defect):
    monkeypatch.setattr(acquisition.socket, "getaddrinfo", lambda *a, **k: dns("8.8.8.8"))
    response = Response()
    response.headers = dict(response.headers)
    if defect == "redirect":
        response.status = 302
        response.headers["Location"] = "https://validate.perfdrive.com/challenge"
    elif defect == "compressed":
        response.headers["Content-Encoding"] = "gzip"
    elif defect == "oversize":
        response.headers["Content-Length"] = str(1048577)
    elif defect == "media":
        response.headers["Content-Type"] = "application/javascript"
    else:
        response.headers["Content-Length"] = "3"
    events = boundary(monkeypatch, response)
    with pytest.raises(ValueError):
        acquisition.fetch(CATALOGUE)
    assert len([e for e in events if e[0] == "connect"]) == 1
    assert len([e for e in events if e[0] == "request"]) == 1
