"""New layout bounds do not alter historical plain text policies."""

import json
from types import SimpleNamespace

import pytest

from mfdataindia.enrichment import amc_book_pdf, layout_book_pdf as decoder


def test_larger_bound_is_source_specific_and_old_decoder_unchanged():
    assert decoder.policy("kotak")["maximum_source_bytes"] == 32 * 1024 * 1024
    assert decoder.policy("uti")["maximum_source_bytes"] == 16 * 1024 * 1024
    assert amc_book_pdf.policy()["maximum_source_bytes"] == 16 * 1024 * 1024
    with pytest.raises(ValueError):
        decoder.policy("caller-override")


def test_physical_page_whitespace_and_original_hashes_are_retained(monkeypatch):
    text = "  AUM       Portfolio\n  1.00      Never mix columns\n"
    monkeypatch.setattr(
        decoder.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(
            returncode=0, stdout=json.dumps({"pages": [text], "document_pages": 1}).encode()
        ),
    )
    result = decoder.extract(b"%PDF-synthetic", profile="kotak")
    assert result["pages"][0]["text"] == text
    assert result["whole_document_semantic_coverage"] is False
    assert result["pages"][0]["page"] == 1


@pytest.mark.parametrize(
    "body",
    [
        {"pages": ["x"], "document_pages": True},
        {"pages": ["x"], "document_pages": 2},
        {"pages": ["x" * 200001], "document_pages": 1},
        {"pages": ["x"], "document_pages": 1, "extra": "unbounded"},
    ],
)
def test_malformed_worker_output_is_rejected(monkeypatch, body):
    monkeypatch.setattr(
        decoder.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=json.dumps(body).encode()),
    )
    with pytest.raises(ValueError):
        decoder.extract(b"%PDF-synthetic", profile="kotak")


def test_uti_does_not_inherit_the_kotak_byte_limit():
    with pytest.raises(ValueError):
        decoder.extract(b"%PDF-" + b"x" * (16 * 1024 * 1024), profile="uti")
