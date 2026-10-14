"""Capture-only adapters: reuse legacy discovery/parsers, never their database writers."""

import hashlib
import json
import os
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import requests

from mfdataindia.enrichment import acquisition
from mfdataindia.ingest.amfi_client import AmfiClient
from mfdataindia.ingest.amfi_nav_history import parse_nav_history_report
from mfdataindia.review.policy import fingerprint

MAX_HISTORY_BYTES = 16 * 1024 * 1024


class CaptureSession(requests.Session):
    """Fixed AMFI history route only; bounded original bytes, no proxy/netrc/redirect."""

    def __init__(self):
        super().__init__()
        self.trust_env = False
        self.original = None

    def get(self, url, **kwargs):
        parsed = urlsplit(url)
        params = kwargs.get("params")
        if (
            url != "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"
            or parsed.query
            or not isinstance(params, dict)
            or set(params) != {"frmdt", "todt", "tp", "mc"}
            or params["mc"] != 0
            or params["tp"] not in (1, 2, 3)
        ):
            raise ValueError("fixed AMFI history coordinates required")
        self.original = None
        kwargs.update(stream=True, allow_redirects=False)
        kwargs["headers"] = {"Accept-Encoding": "identity"}
        response = super().get(url, **kwargs)
        try:
            if response.status_code != 200:
                raise ValueError("AMFI history HTTP status is not 200")
            if response.headers.get("Content-Encoding", "identity") != "identity":
                raise ValueError("compressed AMFI history transport unsupported")
            declared = response.headers.get("Content-Length")
            if declared and (not declared.isdigit() or int(declared) > MAX_HISTORY_BYTES):
                raise ValueError("AMFI history declared size exceeds capture bound")
            content = bytearray()
            for chunk in response.iter_content(65536):
                content.extend(chunk)
                if len(content) > MAX_HISTORY_BYTES:
                    raise ValueError("AMFI history streamed size exceeds capture bound")
            if not content or declared and len(content) != int(declared):
                raise ValueError("empty or incomplete AMFI history body")
            self.original = bytes(content)
            response._content = self.original
            response._content_consumed = True
            return response
        finally:
            response.close()


def archive(root: Path, content: bytes, receipt: dict):
    """Exclusive-create, content-addressed public evidence; no overwrite/deletion."""
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_HISTORY_BYTES:
        raise ValueError("bounded nonempty original source bytes required")
    root = root.resolve(strict=True)
    stat = root.stat()
    if not root.is_dir() or stat.st_uid != os.getuid() or stat.st_mode & 0o077:
        raise ValueError("task-owned private archive directory required")
    checksum = hashlib.sha256(content).hexdigest()
    path = root / (checksum + ".source")
    if path.is_symlink():
        raise ValueError("archive symlinks not permitted")
    try:
        with path.open("xb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError:
        if path.read_bytes() != content:
            raise ValueError("existing source bytes differ from replay")
    document = {
        **receipt,
        "content_hash": checksum,
        "bytes": len(content),
        "source_file": path.name,
        "source_rows_changed": 0,
        "database_imported": False,
        "publication_eligible": False,
    }
    document["receipt_hash"] = fingerprint(document)
    manifest = root / (document["receipt_hash"] + ".receipt.json")
    payload = json.dumps(document, sort_keys=True, indent=2) + "\n"
    if manifest.is_symlink():
        raise ValueError("archive receipt symlinks not permitted")
    try:
        with manifest.open("x") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError:
        if manifest.read_text() != payload:
            raise ValueError("existing receipt differs from replay")
    return document


def capture_history(root: Path, start: date, end: date, *, tp: int):
    with CaptureSession() as session:
        client = AmfiClient(session=session, timeout=20, max_retries=0)
        fetched = client.fetch_nav_history(start, end, tp)
        raw = session.original
        if raw is None or hashlib.sha256(raw).hexdigest() != fetched.content_sha256:
            raise ValueError("lossless original AMFI body unavailable")
        rows, report = parse_nav_history_report(fetched.text)
        invalid = report.quarantined or report.unparsed_nav or report.unparsed_date
        outside = any(r.nav_date is not None and not start <= r.nav_date <= end for r in rows)
        recognized = report.header_rows > 0 and "\ufffd" not in fetched.text
        accepted = bool(recognized and not invalid and not outside)
        # Parsing conformance is not trading-calendar completeness, data
        # correctness, scheme-identity verification or permission to replace NAVs.
        return archive(
            root,
            raw,
            {
                "format": "amfi-history-source-capture-v1",
                "url": fetched.url,
                "from_date": start.isoformat(),
                "to_date": end.isoformat(),
                "scheme_type": tp,
                "observed_at": fetched.fetched_at.isoformat(),
                "http_status": fetched.http_status,
                "parser": "existing-amfi-history-8col",
                "rows": len(rows),
                "quarantined": report.quarantined,
                "unparsed_nav": report.unparsed_nav,
                "unparsed_date": report.unparsed_date,
                "dates_outside_request": outside,
                "recognized_header": recognized,
                "state": ("format_checked" if rows else "no_observations")
                if accepted
                else "quarantined",
                "coverage_complete": False,
            },
        )


def capture_document(
    root: Path,
    url: str,
    hosts: list[str],
    *,
    authority_reference: str,
    use_basis="operator_reported_authorization",
):
    """Acquire reviewed exact official URL; do not copy narrative into an API."""
    from datetime import datetime, timezone

    if (
        use_basis not in ("operator_reported_authorization", "published_permission")
        or not 8 <= len(authority_reference) <= 500
        or any(ord(c) < 32 for c in authority_reference)
    ):
        raise ValueError("non-secret reported source-use reference required")
    amfi_hosts = {"amfiindia.com", "www.amfiindia.com", "portal.amfiindia.com", "api.amfiindia.com"}
    if urlsplit(url).hostname in amfi_hosts and use_basis != "published_permission":
        raise ValueError(
            "AMFI document retention scope must be resolved separately from existing NAV acquisition"
        )
    if urlsplit(url).hostname == "mutualfund.adityabirlacapital.com":
        if "mutualfund.adityabirlacapital.com" not in hosts:
            raise ValueError("explicit ABSL official host required")
        result = acquisition.fetch_absl_document(url)
    else:
        result = acquisition.fetch(url, hosts)
    if urlsplit(result["final_url"]).hostname in amfi_hosts and use_basis != "published_permission":
        raise ValueError("AMFI document retention scope must be resolved separately")
    if result["media_type"] == "application/pdf" and not result["content"].startswith(b"%PDF-"):
        raise ValueError("source media type does not match PDF bytes")
    return archive(
        root,
        result["content"],
        {
            "format": "official-document-source-capture-v1",
            "url": url,
            "final_url": result["final_url"],
            "redirect_chain": result["redirect_chain"],
            "media_type": result["media_type"],
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "state": "raw_only_not_parsed",
            "document_as_of": None,
            "coverage_complete": False,
            "use_basis": use_basis,
            "authority_reference": authority_reference,
            "rights_independently_verified": False,
        },
    )


def discover_absl(root: Path, year: int):
    """Two bounded official requests, retained for replay; no automatic PDF intake."""
    from datetime import datetime, timezone
    from mfdataindia.ingest.amc_factsheets.download import (
        BASE, _LANDING, monthly_documents, AbSLFactsheetClient,
    )

    # Validate coordinates before the first network request.
    if type(year) is not int or not 1900 <= year <= 2100:
        raise ValueError("bounded integer discovery year required")
    hosts = ["mutualfund.adityabirlacapital.com"]
    landing = acquisition.fetch(BASE + _LANDING, hosts)
    api = acquisition.fetch_absl_monthly(landing["content"].decode("utf-8", "strict"), year)
    documents = monthly_documents(json.loads(api["content"]), year)
    observed = datetime.now(timezone.utc).isoformat()
    receipts = [
        archive(root, result["content"], {
            "format": "official-absl-discovery-source-v1",
            "url": result["final_url"],
            "redirect_chain": result["redirect_chain"],
            "media_type": result["media_type"],
            "observed_at": observed,
            "year": year,
            "state": "discovery_only",
            "coverage_complete": False,
        }) for result in (landing, api)
    ]
    newest = max(documents, key=lambda d: AbSLFactsheetClient._period_key(d.period)) if documents else None
    return {
        "format": "official-absl-discovery-v1",
        "observed_at": observed,
        "year": year,
        "source_receipts": [r["receipt_hash"] for r in receipts],
        "documents": [{"title": d.title, "period": d.period, "url": d.url} for d in documents],
        "latest_listed_period": newest.period if newest else None,
        "state": "documents_listed_not_downloaded" if documents else "no_documents",
        "coverage_complete": False,
        "database_imported": False,
        "source_rows_changed": 0,
    }


def extract_document(root: Path, document: Path, expected_sha: str, *, absl_period=None, page_start=1, page_end=None):
    """Archive private extraction separately from original evidence; no SQL loader."""
    from mfdataindia.enrichment.pdf_evidence import extract_pdf, absl_candidates
    from mfdataindia.enrichment.evidence import MAX_BYTES

    with document.open("rb") as source:
        content = source.read(MAX_BYTES + 1)
    if hashlib.sha256(content).hexdigest() != expected_sha:
        raise ValueError("original PDF hash does not match request")
    result = extract_pdf(content, page_start=page_start, page_end=page_end)
    candidates = absl_candidates(result, absl_period) if absl_period else []
    payload = json.dumps({"extraction": result, "candidates": candidates}, sort_keys=True).encode()
    receipt = archive(root, payload, {
        "format": "official-pdf-extraction-capture-v1",
        "source_content_hash": expected_sha,
        "extraction_result_hash": result["result_hash"],
        "policy_hash": result["policy_hash"],
        "extractor_version": result["extractor_version"],
        "pages": len(result["pages"]),
        "requested_page_range": result["requested_page_range"],
        "document_pages": result["document_pages"],
        "whole_document_text_extracted": result["whole_document_text_extracted"],
        "text_pages": sum(bool(p["text"].strip()) for p in result["pages"]),
        "unmapped_candidates": len(candidates),
        "declared_absl_period": absl_period,
        "state": result["state"],
        "facts_accepted": False,
        "coverage_complete": False,
    })
    return receipt
