"""Versioned physical-layout decoder; historical plain-text decoders stay frozen.

Profiles are internal reviewed source policies, not caller-provided limits or
URLs. Neither decoded text nor an issuer's filename certifies a financial date.
"""

import hashlib
import json
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

from mfdataindia.review.policy import fingerprint
from mfdataindia.enrichment import pdf_security_v3

PROFILES = {"kotak": 32 * 1024 * 1024, "uti": 16 * 1024 * 1024}


def policy(profile):
    if profile not in PROFILES:
        raise ValueError("reviewed layout source profile required")
    return {
        "id": "official-amc-physical-layout-text",
        "version": 4,
        "dependency_contract": pdf_security_v3.dependency_contract(),
        "profile": profile,
        "maximum_source_bytes": PROFILES[profile],
        "maximum_pages": 400,
        "maximum_page_characters": 200000,
        "maximum_total_characters": 16000000,
        "maximum_page_stream_bytes": 10000000,
        "maximum_total_stream_bytes": 256000000,
        "worker_wall_seconds": 180,
        "worker_cpu_seconds": 150,
        "linux_worker_address_space_bytes": 1024 * 1024 * 1024,
        "extraction": "pypdf layout mode, physical page order, original whitespace retained, rotated text excluded; no OCR, external calls or pointer inference",
        "dates": "not inferred from filenames, URLs, creation metadata or edition months",
        "coverage": "bounded text evidence only, not whole-document semantic coverage or human verification",
        "source_writes": "none",
    }


def extract(content, *, profile):
    pdf_security_v3.require_runtime()
    limits = policy(profile)
    if (
        not isinstance(content, bytes)
        or not content.startswith(b"%PDF-")
        or not 0 < len(content) <= limits["maximum_source_bytes"]
    ):
        raise ValueError("bounded original layout book required")
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-m", "mfdataindia.enrichment._layout_book_pdf_worker_v4", profile],
            input=content,
            capture_output=True,
            timeout=limits["worker_wall_seconds"],
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ValueError("layout book decoder deadline exceeded") from None
    if result.returncode or len(result.stdout) > 96 * 1024 * 1024:
        raise ValueError("layout book decoder failed or exceeded bounds")
    body = json.loads(result.stdout)
    pages = body.get("pages")
    if (
        set(body) != {"pages", "document_pages"}
        or type(body["document_pages"]) is not int
        or not 0 < body["document_pages"] <= limits["maximum_pages"]
        or not isinstance(pages, list)
        or len(pages) != body["document_pages"]
        or any(not isinstance(p, str) or len(p) > limits["maximum_page_characters"] for p in pages)
        or sum(map(len, pages)) > limits["maximum_total_characters"]
    ):
        raise ValueError("layout book decoder structure differs")
    document = {
        "source_hash": hashlib.sha256(content).hexdigest(),
        "policy_hash": fingerprint(limits),
        "extractor_version": version("pypdf"),
        "engine_hash": hashlib.sha256(
            Path(__file__).read_bytes()
            + Path(__file__).with_name("_layout_book_pdf_worker_v4.py").read_bytes()
            + Path(pdf_security_v3.__file__).read_bytes()
        ).hexdigest(),
        "document_pages": body["document_pages"],
        "whole_document_text_coverage": True,
        "whole_document_semantic_coverage": False,
        "pages": [
            {"page": n, "text": text, "text_hash": hashlib.sha256(text.encode()).hexdigest()}
            for n, text in enumerate(pages, 1)
        ],
    }
    return {**document, "extraction_hash": fingerprint(document)}
