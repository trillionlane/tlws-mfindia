"""Private, bounded PDF text evidence. No original-store writer or acceptance path."""

import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from importlib.metadata import version
from pathlib import Path

from mfdataindia.enrichment.evidence import MAX_BYTES
from mfdataindia.review.policy import fingerprint

_POLICY = {
    "id": "official-pdf-text-evidence",
    "version": 2,
    "engine_version": "pdf-text-evidence-2",
    "extractor": "pypdf plain text; exact installed version recorded in each result",
    "maximum_source_bytes": MAX_BYTES,
    "maximum_pages": 400,
    "page_selection": "explicit inclusive page bounds; partial extraction never claims whole-document coverage",
    "maximum_page_characters": 200000,
    "maximum_total_characters": 2000000,
    "maximum_page_stream_bytes": 10000000,
    "maximum_total_stream_bytes": 40000000,
    "worker_wall_seconds": 30,
    "worker_cpu_seconds": 20,
    "linux_worker_address_space_bytes": 512 * 1024 * 1024,
    "encrypted_documents": "reject; do not decrypt",
    "image_only_pages": "empty text retained; no OCR or inferred content",
    "publication": "private text evidence and unmapped scheme candidates only; never accepted facts",
    "mapping": "no name-only mapping; require separately verified code/ISIN/AMC/plan/option",
    "source_writes": "none",
}


def policy():
    return deepcopy(_POLICY)


def extract_pdf(content: bytes, *, page_start=1, page_end=None):
    """Untrusted PDF decoding happens outside the caller with a hard deadline."""
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_BYTES:
        raise ValueError("bounded original PDF bytes required")
    if not content.startswith(b"%PDF-"):
        raise ValueError("PDF magic required")
    if (
        type(page_start) is not int or not 1 <= page_start <= _POLICY["maximum_pages"]
        or page_end is not None and (type(page_end) is not int or not page_start <= page_end <= _POLICY["maximum_pages"])
    ):
        raise ValueError("bounded inclusive page selection required")
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-m", "mfdataindia.enrichment._pdf_worker",
             "--pages", str(page_start), str(page_end or _POLICY["maximum_pages"])],
            input=content,
            capture_output=True,
            timeout=_POLICY["worker_wall_seconds"],
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ValueError("PDF extraction exceeded worker deadline") from None
    if completed.returncode != 0:
        raise ValueError("PDF extraction failed or exceeded resource bounds")
    if len(completed.stdout) > 12 * 1024 * 1024:
        raise ValueError("PDF worker output exceeds bound")
    decoded = json.loads(completed.stdout)
    pages, document_pages = decoded["pages"], decoded["document_pages"]
    if (
        not isinstance(pages, list)
        or not 0 < len(pages) <= _POLICY["maximum_pages"]
        or type(document_pages) is not int or not 0 < document_pages <= _POLICY["maximum_pages"]
        or len(pages) != min(page_end or _POLICY["maximum_pages"], document_pages) - page_start + 1
        or any(not isinstance(p, str) or len(p) > _POLICY["maximum_page_characters"] for p in pages)
        or sum(map(len, pages)) > _POLICY["maximum_total_characters"]
    ):
        raise ValueError("invalid bounded PDF worker result")
    result = {
        "format": "official-pdf-text-evidence-v1",
        "policy": policy(),
        "policy_hash": fingerprint(policy()),
        "source_content_hash": hashlib.sha256(content).hexdigest(),
        "extractor_version": version("pypdf"),
        "engine_code_hash": hashlib.sha256(
            Path(__file__).read_bytes() + Path(__file__).with_name("_pdf_worker.py").read_bytes()
        ).hexdigest(),
        "requested_page_range": [page_start, page_end],
        "document_pages": document_pages,
        "whole_document_text_extracted": page_start == 1 and len(pages) == document_pages,
        "pages": [
            {"page": index, "text": text, "text_hash": hashlib.sha256(text.encode()).hexdigest()}
            for index, text in enumerate(pages, page_start)
        ],
        "state": "text_extracted_not_verified" if any(p.strip() for p in pages) else "no_text",
        "facts_accepted": False,
        "source_rows_changed": 0,
    }
    result["result_hash"] = fingerprint(result)
    return result


def absl_candidates(extracted, period):
    """Reuse the pure legacy layout parser, never its name matcher or SQL loader.

    Numeric float-based legacy fields are deliberately not emitted: E06 must
    retain original numeric tokens, units and dates before accepting any fact.
    """
    from mfdataindia.ingest.amc_factsheets.download import _MONTH_ORDER

    if not isinstance(period, str) or len(period.split()) != 2:
        raise ValueError("explicit document period required")
    month, year = period.split()
    if month not in _MONTH_ORDER or not year.isdigit() or not 1900 <= int(year) <= 2100:
        raise ValueError("recognized document period required")
    # Caller passes the result produced above, not an API-submitted text blob.
    expected = extracted.get("result_hash")
    if fingerprint({k: v for k, v in extracted.items() if k != "result_hash"}) != expected:
        raise ValueError("PDF extraction seal mismatch")
    pages = extracted["pages"]
    if not 0 < len(pages) <= _POLICY["maximum_pages"] or any(
        not isinstance(p.get("text"), str) or len(p["text"]) > _POLICY["maximum_page_characters"] for p in pages
    ) or sum(len(p["text"]) for p in pages) > _POLICY["maximum_total_characters"]:
        raise ValueError("bounded extracted pages required")
    if type(pages[0].get("page")) is not int or not 1 <= pages[0]["page"] <= _POLICY["maximum_pages"] or [p.get("page") for p in pages] != list(range(pages[0]["page"], pages[0]["page"] + len(pages))):
        raise ValueError("contiguous original page numbers required")
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-m", "mfdataindia.enrichment._pdf_worker", "--absl-candidates", period],
            input=json.dumps(pages).encode(), capture_output=True,
            timeout=_POLICY["worker_wall_seconds"], check=False,
        )
    except subprocess.TimeoutExpired:
        raise ValueError("candidate parsing exceeded worker deadline") from None
    if completed.returncode != 0 or len(completed.stdout) > 1024 * 1024:
        raise ValueError("candidate parsing failed or exceeded resource bounds")
    return json.loads(completed.stdout)


def _absl_candidates_from_pages(pages, period):
    from mfdataindia.ingest.amc_factsheets import AbSLParser

    return [
        {
            "scheme_name": item.scheme_name,
            "document_period": item.factsheet_period,
            "nav_as_of": item.nav_date.isoformat() if item.nav_date else None,
            "detail_page": item.page + pages[0]["page"] - 1,
            "amc_parser": "absl-legacy-layout-candidate-v1",
            "state": "unmapped_candidate",
            "facts_accepted": False,
            "requires": ["code", "ISIN", "AMC", "plan", "option", "field_evidence_verification"],
        }
        for item in AbSLParser(period).parse_pages(p["text"] for p in pages)
    ]
