"""Actual admission CLI + urllib redirects, with a provider-free HTTPS wire.

The observed Blob failure is an HTTP response, not a mocked admission decision.
Neither this transport nor the production client exposes mutation methods.
"""
from email.message import Message
from http import HTTPStatus
import io
import json
import sys
import urllib.parse
import urllib.request
import urllib.response

import pytest

from test_release_admission import FakeAPI, SHA, admission, packet_archive

TOKEN = "synthetic-github-secret-never-report"
API = "https://api.github.com/"
ARCHIVE = API + "repos/" + admission.REPOSITORY + "/actions/artifacts/456/zip"
BLOB = "https://productionresultssa3.blob.core.windows.net/synthetic/archive.zip?sig=synthetic-signed-secret"


@pytest.fixture
def wire(monkeypatch):
    evidence = FakeAPI()
    evidence.artifact["archive_download_url"] = ARCHIVE
    calls = []
    config = {"code": 302, "location": BLOB, "blob_status": 200, "api_status": 200, "chain": {}}

    def response(request, code, body=b"", location=None):
        headers = Message()
        if location:
            headers["Location"] = location
        result = urllib.response.addinfourl(io.BytesIO(body), headers, request.full_url, code)
        result.msg = HTTPStatus(code).phrase
        return result

    def open_https(_handler, request):
        assert request.get_method() == "GET", "read-only admission attempted a mutation"
        calls.append({"url": request.full_url, "headers": dict(request.header_items())})
        url = request.full_url
        auth = request.get_header("Authorization")
        if urllib.parse.urlparse(url).hostname == "api.github.com" and config["api_status"] != 200:
            return response(request, config["api_status"], b"synthetic-private-error")
        if url == ARCHIVE:
            assert auth == "Bearer " + TOKEN
            return response(request, config["code"], location=config["location"])
        if url in config["chain"]:
            return response(request, 302, location=config["chain"][url])
        if urllib.parse.urlparse(url).hostname != "api.github.com":
            # Real Blob observation: an API Bearer header makes this fail.
            status = 401 if auth else config["blob_status"]
            body = b"synthetic-private-error"
            if status == 200:
                body = packet_archive(evidence.packet)
                config["archive_bytes"] = body
            return response(request, status, body)
        assert auth == "Bearer " + TOKEN
        path = url.removeprefix(API)
        return response(request, 200, json.dumps(evidence.json(path)).encode())

    def no_plain_http(_handler, _request):
        pytest.fail("HTTPS downgrade reached the wire")

    # Both the base urlopen and corrected dedicated opener use this actual
    # urllib HTTPS layer; the redirect engine itself is not mocked.
    monkeypatch.setattr(urllib.request.HTTPSHandler, "https_open", open_https)
    monkeypatch.setattr(urllib.request.HTTPHandler, "http_open", no_plain_http)
    monkeypatch.setattr(urllib.request, "_opener", None)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    return evidence, config, calls


def cli(monkeypatch, tmp_path):
    output, summary = tmp_path / "result.json", tmp_path / "summary.md"
    monkeypatch.setattr(sys, "argv", ["release_admission.py", "consume",
        "--run-id", "123", "--run-attempt", "2", "--candidate-sha", SHA,
        "--output", str(output), "--summary", str(summary)])
    return output, summary


@pytest.mark.parametrize("redirect_code", [302, 303, 307, 308])
def test_actual_cli_admits_authenticated_api_then_credential_free_blob_redirect(
        wire, monkeypatch, tmp_path, capsys, redirect_code):
    _evidence, config, calls = wire
    config["code"] = redirect_code
    output, summary = cli(monkeypatch, tmp_path)
    admission.main()
    result = json.loads(output.read_text())
    assert result["release_admitted"] is True
    assert result["candidate_sha"] == SHA and result["run_attempt"] == 2
    assert result["permission_enabled"] is result["deployment_performed"] is False
    assert result["consumer_acceptance"] == "PENDING_RO04"
    blob = [call for call in calls if call["url"] == BLOB]
    assert len(blob) == 1 and "Authorization" not in blob[0]["headers"]
    assert len(calls) == 7  # Six evidence resources, one redirected Blob GET.
    text = capsys.readouterr().out + summary.read_text() + output.read_text()
    assert TOKEN not in text and "synthetic-signed-secret" not in text


def test_cross_origin_redirect_chain_never_reintroduces_api_credential(wire):
    _evidence, config, calls = wire
    second = "https://productionresultssa4.blob.core.windows.net/synthetic/next.zip?sig=synthetic-signed-secret"
    config["chain"][BLOB] = second
    raw = admission.GitHubAPI(TOKEN).bytes(ARCHIVE)
    assert raw == config["archive_bytes"]
    assert admission._packet_from_archive(raw) == _evidence.packet
    assert len(calls) == 3
    assert all("Authorization" not in row["headers"] for row in calls[1:])


def test_same_origin_redirect_keeps_api_authentication_before_blob_handoff(wire):
    evidence, config, calls = wire
    intermediate = API + "same-origin-artifact-hop"
    config["location"] = intermediate
    config["chain"][intermediate] = BLOB
    raw = admission.GitHubAPI(TOKEN).bytes(ARCHIVE)
    assert raw == config["archive_bytes"]
    assert admission._packet_from_archive(raw) == evidence.packet
    assert calls[1]["headers"]["Authorization"] == "Bearer " + TOKEN
    assert "Authorization" not in calls[2]["headers"]


@pytest.mark.parametrize("location", [
    "http://example.invalid/unsafe?sig=synthetic-signed-secret",
    "https://user:synthetic-signed-secret@productionresultssa3.blob.core.windows.net/archive.zip",
])
def test_unsafe_redirect_stops_before_target_transport_and_never_reports_url(wire, location):
    _evidence, config, calls = wire
    config["location"] = location
    with pytest.raises(admission.AdmissionError) as stopped:
        admission.GitHubAPI(TOKEN).bytes(ARCHIVE)
    assert len(calls) == 1
    assert TOKEN not in str(stopped.value) and "synthetic-signed-secret" not in str(stopped.value)


@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_blob_failure_keeps_cli_unadmitted_without_retry_or_sensitive_output(
        wire, monkeypatch, tmp_path, capsys, status):
    _evidence, config, calls = wire
    config["blob_status"] = status
    output, summary = cli(monkeypatch, tmp_path)
    with pytest.raises(admission.AdmissionError, match="HTTP " + str(status)) as stopped:
        admission.main()
    assert not output.exists() and not summary.exists()
    assert len(calls) == 6 and sum(row["url"] == BLOB for row in calls) == 1
    text = str(stopped.value) + repr(capsys.readouterr())
    assert TOKEN not in text and "synthetic-signed-secret" not in text and "synthetic-private-error" not in text


def test_redirect_does_not_bypass_packet_integrity_or_candidate_binding(wire, monkeypatch, tmp_path):
    evidence, _config, calls = wire
    evidence.packet["candidate_sha"] = "b" * 40
    output, _summary = cli(monkeypatch, tmp_path)
    with pytest.raises(admission.AdmissionError, match="digest mismatch"):
        admission.main()
    assert not output.exists() and len(calls) == 6


def test_blob_url_cannot_be_supplied_as_an_initial_evidence_url(wire):
    _evidence, _config, calls = wire
    with pytest.raises(admission.AdmissionError, match="configured API origin"):
        admission.GitHubAPI(TOKEN).bytes(BLOB)
    assert calls == []


@pytest.mark.parametrize("status", [401, 403])
def test_initial_api_auth_failure_never_falls_back_to_anonymous_or_retries(wire, status):
    _evidence, config, calls = wire
    config["api_status"] = status
    with pytest.raises(admission.AdmissionError, match="HTTP " + str(status)):
        admission.GitHubAPI(TOKEN).json("repos/" + admission.REPOSITORY + "/actions/runs/123")
    assert len(calls) == 1 and calls[0]["headers"]["Authorization"] == "Bearer " + TOKEN


def test_scheme_relative_blob_redirect_still_removes_the_api_credential(wire):
    evidence, config, calls = wire
    config["location"] = BLOB.removeprefix("https:")
    raw = admission.GitHubAPI(TOKEN).bytes(ARCHIVE)
    assert raw == config["archive_bytes"]
    assert admission._packet_from_archive(raw) == evidence.packet
    assert len(calls) == 2 and "Authorization" not in calls[-1]["headers"]


def test_redirect_loop_remains_bounded_and_does_not_reintroduce_authorization(wire):
    _evidence, config, calls = wire
    config["chain"][BLOB] = BLOB
    with pytest.raises(admission.AdmissionError, match="HTTP 302") as stopped:
        admission.GitHubAPI(TOKEN).bytes(ARCHIVE)
    assert 2 <= len(calls) <= 6
    assert all("Authorization" not in row["headers"] for row in calls[1:])
    assert TOKEN not in str(stopped.value) and "synthetic-signed-secret" not in str(stopped.value)
