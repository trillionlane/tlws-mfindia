"""Decoder mechanics only; synthetic PDFs do not establish issuer coverage."""

import hashlib
import io
import json
import subprocess
from types import SimpleNamespace

from pypdf import PdfWriter
import pytest

from mfdataindia.enrichment import amc_book_pdf, monthly_pdf


def test_actual_worker_retains_physical_pages_without_semantic_claim():
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    output = io.BytesIO()
    writer.write(output)
    raw = output.getvalue()
    result = amc_book_pdf.extract(raw)
    assert result["source_hash"] == hashlib.sha256(raw).hexdigest()
    assert result["document_pages"] == 1
    assert result["pages"] == [
        {"page": 1, "text": "", "text_hash": hashlib.sha256(b"").hexdigest()}
    ]
    assert result["whole_document_text_coverage"] is True
    assert result["whole_document_semantic_coverage"] is False


@pytest.mark.parametrize(
    "raw", [b"", b"not-pdf", "not-bytes", b"%PDF-" + b"x" * (16 * 1024 * 1024)]
)
def test_source_bounds_before_subprocess(monkeypatch, raw):
    def forbidden(*args, **kwargs):
        raise AssertionError("unbounded source reached worker")

    monkeypatch.setattr(amc_book_pdf.subprocess, "run", forbidden)
    with pytest.raises(ValueError, match="bounded original"):
        amc_book_pdf.extract(raw)


@pytest.mark.parametrize(
    "body",
    [
        {"document_pages": 401, "pages": []},
        {"document_pages": True, "pages": [""]},
        {"document_pages": 1, "pages": []},
        {"document_pages": 1, "pages": [123]},
        {"document_pages": 1, "pages": ["x" * 200001]},
        {"document_pages": 1, "pages": [""], "unexpected": True},
    ],
)
def test_worker_output_is_not_trusted(monkeypatch, body):
    monkeypatch.setattr(
        amc_book_pdf.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps(body).encode()),
    )
    with pytest.raises(ValueError, match="worker output differs"):
        amc_book_pdf.extract(b"%PDF-synthetic")


def test_deadline_and_nonzero_never_publish_pages(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("bounded worker", 150)

    monkeypatch.setattr(amc_book_pdf.subprocess, "run", timeout)
    with pytest.raises(ValueError, match="deadline"):
        amc_book_pdf.extract(b"%PDF-synthetic")
    monkeypatch.setattr(
        amc_book_pdf.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout=b"{}")
    )
    with pytest.raises(ValueError, match="failed"):
        amc_book_pdf.extract(b"%PDF-synthetic")


def test_old_monthly_decoder_and_mutable_policy_copy_remain_separate():
    assert monthly_pdf.policy()["maximum_selected_pages"] == 24
    policy = amc_book_pdf.policy()
    policy["maximum_pages"] = 10000
    assert amc_book_pdf.policy()["maximum_pages"] == 400
