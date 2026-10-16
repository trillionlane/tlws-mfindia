"""Versioned larger official-book decoder; the older 8 MiB policy stays frozen."""

import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from importlib.metadata import version
from pathlib import Path

from mfdataindia.review.policy import fingerprint

_POLICY = {
    "id": "official-monthly-book-text",
    "version": 1,
    "maximum_source_bytes": 16 * 1024 * 1024,
    "maximum_pages": 400,
    "maximum_selected_pages": 24,
    "maximum_page_characters": 200000,
    "maximum_total_characters": 2000000,
    "maximum_page_stream_bytes": 10000000,
    "maximum_total_stream_bytes": 40000000,
    "worker_wall_seconds": 45,
    "worker_cpu_seconds": 30,
    "linux_worker_address_space_bytes": 512 * 1024 * 1024,
    "extraction": "pypdf plain text; original page and exact text hashes; no OCR, riskometer pointer inference or whole-book coverage claim",
    "source_writes": "none",
}


def policy():
    return deepcopy(_POLICY)


def extract(content, *, start, end):
    if (
        not isinstance(content, bytes)
        or not content.startswith(b"%PDF-")
        or not 0 < len(content) <= _POLICY["maximum_source_bytes"]
        or type(start) is not int
        or type(end) is not int
        or not 1 <= start <= end <= _POLICY["maximum_pages"]
        or end - start + 1 > _POLICY["maximum_selected_pages"]
    ):
        raise ValueError("bounded original book and explicit page window required")
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-m",
                "mfdataindia.enrichment._monthly_pdf_worker",
                str(start),
                str(end),
            ],
            input=content,
            capture_output=True,
            timeout=_POLICY["worker_wall_seconds"],
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ValueError("monthly book extraction deadline exceeded") from None
    if result.returncode or len(result.stdout) > 12 * 1024 * 1024:
        raise ValueError("monthly book extraction failed or exceeded bounds")
    body = json.loads(result.stdout)
    pages = body["pages"]
    if (
        type(body["document_pages"]) is not int
        or not end <= body["document_pages"] <= _POLICY["maximum_pages"]
        or not isinstance(pages, list)
        or len(pages) != end - start + 1
        or any(not isinstance(t, str) or len(t) > _POLICY["maximum_page_characters"] for t in pages)
        or sum(map(len, pages)) > _POLICY["maximum_total_characters"]
    ):
        raise ValueError("monthly book worker output differs")
    result = {
        "source_hash": hashlib.sha256(content).hexdigest(),
        "policy_hash": fingerprint(policy()),
        "extractor_version": version("pypdf"),
        "engine_hash": hashlib.sha256(
            Path(__file__).read_bytes()
            + Path(__file__).with_name("_monthly_pdf_worker.py").read_bytes()
        ).hexdigest(),
        "document_pages": body["document_pages"],
        "whole_document_coverage": False,
        "pages": [
            {"page": number, "text": text, "text_hash": hashlib.sha256(text.encode()).hexdigest()}
            for number, text in enumerate(pages, start)
        ],
    }
    return {**result, "extraction_hash": fingerprint(result)}
