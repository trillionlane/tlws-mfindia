"""Bounded whole-book text evidence, separate from frozen pilot page policies."""

import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from importlib.metadata import version
from pathlib import Path

from mfdataindia.review.policy import fingerprint

_POLICY = {
    "id": "official-amc-book-text",
    "version": 1,
    "maximum_source_bytes": 16 * 1024 * 1024,
    "maximum_pages": 400,
    "maximum_page_characters": 200000,
    "maximum_total_characters": 8000000,
    "maximum_page_stream_bytes": 10000000,
    "maximum_total_stream_bytes": 160000000,
    "worker_wall_seconds": 150,
    "worker_cpu_seconds": 120,
    "linux_worker_address_space_bytes": 768 * 1024 * 1024,
    "extraction": "pypdf plain text in physical page order; original/page/text/code hashes; no OCR, executable content or riskometer-pointer inference",
    "coverage": "all bounded pages inspected for text, not whole-factsheet semantic extraction or validated values",
    "source_writes": "none",
}


def policy():
    return deepcopy(_POLICY)


def extract(content):
    if (
        not isinstance(content, bytes)
        or not content.startswith(b"%PDF-")
        or not 0 < len(content) <= _POLICY["maximum_source_bytes"]
    ):
        raise ValueError("bounded original AMC book required")
    try:
        run = subprocess.run(
            [sys.executable, "-I", "-m", "mfdataindia.enrichment._amc_book_pdf_worker"],
            input=content,
            capture_output=True,
            timeout=_POLICY["worker_wall_seconds"],
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ValueError("AMC book extraction deadline exceeded") from None
    if run.returncode or len(run.stdout) > 48 * 1024 * 1024:
        raise ValueError("AMC book extraction failed or exceeded bounds")
    body = json.loads(run.stdout)
    pages = body.get("pages")
    if (
        set(body) != {"pages", "document_pages"}
        or type(body["document_pages"]) is not int
        or not 0 < body["document_pages"] <= _POLICY["maximum_pages"]
        or not isinstance(pages, list)
        or len(pages) != body["document_pages"]
        or any(not isinstance(t, str) or len(t) > _POLICY["maximum_page_characters"] for t in pages)
        or sum(map(len, pages)) > _POLICY["maximum_total_characters"]
    ):
        raise ValueError("AMC book worker output differs")
    result = {
        "source_hash": hashlib.sha256(content).hexdigest(),
        "policy_hash": fingerprint(policy()),
        "extractor_version": version("pypdf"),
        "engine_hash": hashlib.sha256(
            Path(__file__).read_bytes()
            + Path(__file__).with_name("_amc_book_pdf_worker.py").read_bytes()
        ).hexdigest(),
        "document_pages": body["document_pages"],
        "whole_document_text_coverage": True,
        "whole_document_semantic_coverage": False,
        "pages": [
            {"page": n, "text": text, "text_hash": hashlib.sha256(text.encode()).hexdigest()}
            for n, text in enumerate(pages, 1)
        ],
    }
    return {**result, "extraction_hash": fingerprint(result)}
