"""Hermetic transport tests. No provider contacted, no TLS/IAM certification claim."""

import pytest

from mfdataindia.enrichment import acquisition


class Response:
    def __init__(self, data=b"<html>fixture</html>", *, status=200, headers=None):
        self.status = status
        self.data = data
        self.headers = {
            "Content-Type": "text/html",
            "Content-Length": str(len(data)),
            **(headers or {}),
        }

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read1(self, count):
        result, self.data = self.data[:count], self.data[count:]
        return result


def transport(monkeypatch, responses):
    calls = []

    class Connection:
        sock = None

        def __init__(self, host, address, timeout):
            calls.append({"host": host, "address": address, "timeout": timeout})

        def request(self, method, path, headers):
            calls[-1].update(method=method, path=path, headers=headers)

        def getresponse(self):
            return responses.pop(0)

        def close(self):
            calls[-1]["closed"] = True

    monkeypatch.setattr(
        acquisition.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))]
    )
    monkeypatch.setattr(acquisition, "PinnedHTTPS", Connection)
    return calls


def test_one_numeric_address_no_proxy_cookie_or_query_transport(monkeypatch):
    calls = transport(monkeypatch, [Response()])
    result = acquisition.fetch("https://official.example/facts", ["official.example"])
    assert result["content"] == b"<html>fixture</html>"
    assert result["redirect_chain"] == ["https://official.example/facts"]
    assert calls[0]["address"] == "93.184.216.34" and calls[0]["closed"]
    assert calls[0]["headers"]["Accept-Encoding"] == "identity"
    assert "Cookie" not in calls[0]["headers"] and "Authorization" not in calls[0]["headers"]


@pytest.mark.parametrize(
    "location",
    [
        "https://unapproved.example/facts",
        "http://official.example/facts",
        "https://127.0.0.1/facts",
        "https://official.example/facts?token=secret",
        "https://official.example:8443/facts",
    ],
)
def test_redirect_validation_before_second_socket(monkeypatch, location):
    calls = transport(monkeypatch, [Response(status=302, headers={"Location": location})])
    with pytest.raises(ValueError):
        acquisition.fetch("https://official.example/facts", ["official.example"])
    assert len(calls) == 1 and calls[0]["closed"]


def test_approved_relative_redirect_records_exact_chain(monkeypatch):
    calls = transport(monkeypatch, [Response(status=302, headers={"Location": "/new"}), Response()])
    result = acquisition.fetch("https://official.example/old", ["official.example"])
    assert result["final_url"] == "https://official.example/new"
    assert result["redirect_chain"] == [
        "https://official.example/old",
        "https://official.example/new",
    ]
    assert len(calls) == 2


@pytest.mark.parametrize(
    "response",
    [
        Response(status=401),
        Response(headers={"Content-Encoding": "gzip"}),
        Response(headers={"Content-Length": "100000000"}),
        Response(headers={"Content-Type": "application/octet-stream"}),
        Response(b""),
        Response(b"short", headers={"Content-Length": "6"}),
    ],
)
def test_failed_compressed_oversized_or_partial_payload_is_not_returned(monkeypatch, response):
    calls = transport(monkeypatch, [response])
    with pytest.raises(ValueError):
        acquisition.fetch("https://official.example/facts", ["official.example"])
    assert calls[0]["closed"]


def test_streaming_bound_even_without_content_length(monkeypatch):
    response = Response(b"123456", headers={"Content-Length": None})
    transport(monkeypatch, [response])
    with pytest.raises(ValueError, match="streamed"):
        acquisition.fetch("https://official.example/facts", ["official.example"], maximum=5)


def test_redirect_loop_is_bounded(monkeypatch):
    calls = transport(
        monkeypatch, [Response(status=302, headers={"Location": "/loop"}) for _ in range(4)]
    )
    with pytest.raises(ValueError, match="redirect limit"):
        acquisition.fetch("https://official.example/facts", ["official.example"])
    assert len(calls) == 4


def test_overall_transfer_deadline_includes_elapsed_headers(monkeypatch):
    calls = transport(monkeypatch, [Response()])
    times = iter((0, 1, 31))
    monkeypatch.setattr(acquisition.time, "monotonic", lambda: next(times))
    with pytest.raises(TimeoutError):
        acquisition.fetch("https://official.example/facts", ["official.example"])
    assert calls[0]["closed"]


def test_tls_uses_original_host_with_the_checked_numeric_connection(monkeypatch):
    captured = {}

    class Sock:
        def close(self):
            captured["closed"] = True

    class TLS:
        def wrap_socket(self, sock, server_hostname):
            captured["hostname"] = server_hostname
            return sock

    def connect(address, timeout):
        captured.update(address=address, timeout=timeout)
        return Sock()

    # Preserve the real secure context initialization, then replace only
    # the socket wrapper in this hermetic test.
    connection = acquisition.PinnedHTTPS("official.example", "93.184.216.34", 10)
    connection._context = TLS()
    monkeypatch.setattr(acquisition.socket, "create_connection", connect)
    connection.connect()
    assert captured == {
        "address": ("93.184.216.34", 443),
        "timeout": 10,
        "hostname": "official.example",
    }
    connection.close()
