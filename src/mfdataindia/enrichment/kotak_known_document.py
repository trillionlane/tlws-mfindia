"""Dated retained discovery evidence, never a claim of live catalogue access."""

import base64
from datetime import datetime, timezone
import hashlib

from mfdataindia.enrichment import kotak_book
from mfdataindia.review.policy import fingerprint

CATALOGUE_URL = kotak_book.CATALOGUE + "?year=2026&month=9&type=month"
CATALOGUE_SHA256 = "2c9b82cb4584bd88506d906ba0e9330ea3714039a4690de881115f94dc1de111"
PDF_SHA256 = "fba08af16c51879d060c867759126b906698a54a2ff237fa7803cac11d760d36"
# Actual retained 545-byte HTTP original; not JSON reserialization or a fresh GET.
ORIGINAL_BASE64 = (
    "eyJzdGF0dXMiOiJTdWNjZXNzIiwic3RhdHVzQ29kZSI6IjIwMCIsInN0YXR1c01lc3NhZ2UiOiJzdWNjZXNzZnVsbHkgcmV0cmlldmVkIGRhdGEgZm9yIHN1YiBoZWFkZXJzIiwic3ViSGVhZGVyTGlzdCI6W3sic3ViSGVhZGVyVGl0bGUiOiJGYWN0c2hlZXQgZm9yIFNlcHRlbWJlciAyMDI2IiwiaGVhZGVyVGl0bGUiOiJGYWN0c2hlZXQiLCJzdWJIZWFkZXJJZCI6MTg3ODEsImhlYWRlcklkIjoyMTksInN1YkhlYWRlck9yZGVyIjo5OTk5OTksImRlc2NyaXB0aW9uIjoiRmFjdHNoZWV0IGZvciBTZXB0ZW1iZXIgMjAyNiIsImNhbGVuZGFyIjoiTU0vWVlZWSIsImRhdGUiOjE3ODgyMjA4MDAwMDAsImNvbnRlbnRUeXBlIjoidXBsb2FkIiwiY29udGVudCI6IkZvcm1zRG93bmxvYWRzL0ZhY3RzaGVldC9GYWN0c2hlZXQtZm9yLVNlcHRlbWJlci0yMDI2L0tvdGFrTUZGYWN0c2hlZXRTZXB0ZW1iZXIyMDI2LnBkZiIsImZpbGVOYW1lIjoiS290YWtNRkZhY3RzaGVldFNlcHRlbWJlcjIwMjYucGRmIiwib3B0aW9uSWQiOm51bGwsImZpbGVDaGFuZ2UiOm51bGx9XX0="
)


def policy():
    return {
        "id": "kotak-reviewed-known-document-v1",
        "version": 1,
        "expected_period": "2026-09-30",
        "catalogue_url": CATALOGUE_URL,
        "catalogue_sha256": CATALOGUE_SHA256,
        "catalogue_bytes": 545,
        "pdf_sha256": PDF_SHA256,
        "reviewed_on": "2026-10-17",
        "valid_before": "2026-11-01",
        "use": "acquisition failure only; never override an acquired contradictory catalogue",
        "freshness": "known document, not live catalogue discovery or whole-AMC completeness",
        "access": "ordinary approved PDF URL only; no redirect, challenge, cookie or retry bypass",
    }


def authority():
    return {
        "mode": "reviewed_known_document_not_live_catalogue",
        "policy_hash": fingerprint(policy()),
        "catalogue_original_sha256": CATALOGUE_SHA256,
        "catalogue_source_url": CATALOGUE_URL,
        "expected_pdf_sha256": PDF_SHA256,
        "reviewed_on": "2026-10-17",
        "valid_before": "2026-11-01",
        "latest_catalogue_discovery_available": False,
    }


def original(request, observed):
    if not isinstance(observed, datetime) or observed.utcoffset() is None:
        raise ValueError("explicit acquisition time required")
    day = observed.astimezone(timezone.utc).date().isoformat()
    if request["expected_period"] != "2026-09-30" or not "2026-10-17" <= day < "2026-11-01":
        return None
    if request["source_url"] != CATALOGUE_URL:
        raise ValueError("exact retained catalogue endpoint required")
    raw = base64.b64decode(ORIGINAL_BASE64, validate=True)
    if len(raw) != 545 or hashlib.sha256(raw).hexdigest() != CATALOGUE_SHA256:
        raise ValueError("actual retained catalogue original differs")
    return {"source_url": CATALOGUE_URL, "content": raw,
            "media_type": "application/json", "role": "catalogue"}
