"""Capture-only source tests; no providers or database writers contacted."""

import hashlib
import json
from datetime import date

import pytest

from mfdataindia.enrichment import source_capture as capture
from mfdataindia.ingest.amfi_client import AmfiClient


@pytest.mark.parametrize("days", [90, 91, 120])
def test_direct_history_over_90_inclusive_days_never_fetches(days):
    from datetime import timedelta

    client = AmfiClient()
    client._get = lambda *a, **k: pytest.fail("must reject before fetch")
    with pytest.raises(ValueError, match="90 inclusive"):
        client.fetch_nav_history(date(2026, 1, 1), date(2026, 1, 1) + timedelta(days=days), 1)


@pytest.mark.parametrize("chunk", [0, 91, 999, True, 1.5])
def test_unbounded_chunk_generation_is_rejected(chunk):
    with pytest.raises(ValueError, match="between 1 and 90"):
        list(
            AmfiClient.iter_nav_history_ranges(
                date(2026, 1, 1), date(2026, 12, 31), chunk_days=chunk
            )
        )


def test_long_history_is_a_series_of_disjoint_90_day_requests_not_a_retention_limit():
    ranges = list(AmfiClient.iter_nav_history_ranges(date(2019, 1, 1), date(2026, 1, 1)))
    assert len(ranges) > 3
    assert ranges[0][:2] == (date(2019, 1, 1), date(2019, 3, 31))
    assert ranges[-1][1] == date(2026, 1, 1)
    assert all((end - start).days < 90 for start, end, _ in ranges)


def test_archive_replay_preserves_original_bytes_and_receipt(tmp_path):
    tmp_path.chmod(0o700)
    raw = b"original\r\nUTF-8 \xe2\x80\x99 and legacy \x96\n"
    first = capture.archive(tmp_path, raw, {"format": "synthetic-source-only"})
    assert capture.archive(tmp_path, raw, {"format": "synthetic-source-only"}) == first
    assert (tmp_path / first["source_file"]).read_bytes() == raw
    assert first["content_hash"] == hashlib.sha256(raw).hexdigest()
    assert first["source_rows_changed"] == 0 and first["database_imported"] is False
    receipt = tmp_path / (first["receipt_hash"] + ".receipt.json")
    assert json.loads(receipt.read_text()) == first
    assert len(list(tmp_path.iterdir())) == 2
    (tmp_path / first["source_file"]).write_bytes(b"tampered fixture")
    with pytest.raises(ValueError, match="differ"):
        capture.archive(tmp_path, raw, {"format": "synthetic-source-only"})
    assert (tmp_path / first["source_file"]).read_bytes() == b"tampered fixture"


def test_archive_cannot_follow_source_symlink_or_use_shared_directory(tmp_path):
    tmp_path.chmod(0o700)
    target = tmp_path / "untouched-fixture"
    target.write_bytes(b"preserved")
    raw = b"source"
    (tmp_path / (hashlib.sha256(raw).hexdigest() + ".source")).symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        capture.archive(tmp_path, raw, {})
    assert target.read_bytes() == b"preserved"
    tmp_path.chmod(0o755)
    with pytest.raises(ValueError, match="private archive"):
        capture.archive(tmp_path, b"other", {})


def test_document_capture_retains_declared_permission_without_claiming_facts(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    monkeypatch.setattr(
        capture.acquisition,
        "fetch",
        lambda *a, **k: {
            "content": b"%PDF-1.7\nfixture",
            "final_url": "https://official.example/facts.pdf",
            "redirect_chain": ["https://official.example/facts.pdf"],
            "media_type": "application/pdf",
        },
    )
    result = capture.capture_document(
        tmp_path,
        "https://official.example/facts.pdf",
        ["official.example"],
        authority_reference="SYNTHETIC REPORTED AUTHORITY ONLY",
    )
    assert result["publication_eligible"] is False and result["state"] == "raw_only_not_parsed"
    assert result["rights_independently_verified"] is False
    assert result["document_as_of"] is None


def test_amfi_document_scope_is_not_silently_inferred_from_nav_acquisition(tmp_path, monkeypatch):
    monkeypatch.setattr(capture.acquisition, "fetch", lambda *a, **k: pytest.fail("must not fetch"))
    with pytest.raises(ValueError, match="retention scope"):
        capture.capture_document(
            tmp_path,
            "https://www.amfiindia.com/directory",
            ["www.amfiindia.com"],
            authority_reference="SYNTHETIC REPORTED AUTHORITY ONLY",
        )


@pytest.mark.parametrize(
    "status,headers,data",
    [
        (302, {}, b"x"),
        (200, {"Content-Encoding": "gzip"}, b"x"),
        (200, {"Content-Length": "5"}, b"x"),
        (200, {}, b""),
    ],
)
def test_history_session_does_not_return_redirect_compressed_empty_or_partial_bytes(
    monkeypatch, status, headers, data
):
    import requests

    class Response:
        status_code = status

        def __init__(self):
            self.headers = headers
            self.closed = False

        def iter_content(self, size):
            yield data

        def close(self):
            self.closed = True

    response = Response()
    monkeypatch.setattr(requests.Session, "get", lambda *a, **k: response)
    with capture.CaptureSession() as session:
        assert session.trust_env is False
        with pytest.raises(ValueError):
            session.get(
                "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx",
                params={"frmdt": "02-Jan-2020", "todt": "02-Jan-2020", "tp": 1, "mc": 0},
            )
        assert session.original is None and response.closed


def test_history_session_preserves_original_bytes_not_decoded_replacements(monkeypatch):
    import requests

    raw = b"original\r\nlegacy \x96"

    class Response:
        status_code = 200
        headers = {"Content-Length": str(len(raw))}

        def iter_content(self, size):
            yield raw

        def close(self):
            pass

    monkeypatch.setattr(requests.Session, "get", lambda *a, **k: Response())
    with capture.CaptureSession() as session:
        result = session.get(
            "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx",
            params={"frmdt": "02-Jan-2020", "todt": "02-Jan-2020", "tp": 1, "mc": 0},
        )
        assert session.original == raw and result._content == raw


def test_capture_session_rejects_unreviewed_endpoints_before_network(monkeypatch):
    import requests

    monkeypatch.setattr(requests.Session, "get", lambda *a, **k: pytest.fail("must not fetch"))
    with capture.CaptureSession() as session:
        with pytest.raises(ValueError, match="fixed AMFI"):
            session.get(
                "https://other.example/history",
                params={"frmdt": "02-Jan-2020", "todt": "02-Jan-2020", "tp": 1, "mc": 0},
            )


def test_empty_source_is_never_archived(tmp_path):
    with pytest.raises(ValueError, match="nonempty"):
        capture.archive(tmp_path, b"", {})
