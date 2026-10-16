"""Versioned recurring source capture/publication policy; not a market-data model."""

from copy import deepcopy

from mfdataindia.enrichment import monthly_current, monthly_facts, monthly_pdf
from mfdataindia.review.policy import fingerprint

ARCHIVE_BUCKET = "tlws_mf_data_source"
ARCHIVE_PREFIX = "official-monthly-facts/absl-large-cap/"
_POLICY = {
    "id": "official-monthly-facts-refresh",
    "version": 1,
    "cohort": [103173, 103174],
    "source": "official ABSL landing and constructed Sitecore monthly catalog, then its latest listed consolidated book",
    "maximum_catalog_requests": 3,
    "maximum_book_requests": 1,
    "maximum_book_bytes": 16 * 1024 * 1024,
    "book_transport_deadline_seconds": 120,
    "transport": "all DNS answers public, connect to a pinned numeric address with original-host TLS, no proxy/netrc/cookie/redirect/decompression/retry; fixed same-origin Referer",
    "discovery": "current UTC year; previous-year fallback only if current-year catalog has no document; publication month must not be in the future",
    "page_discovery": "first 24 pages only; exactly one whole-line fund title with Fund Details and explicit NAV as-on date; adjacent same-fund changes page; fail closed on layout drift",
    "pdf_policy_hash": fingerprint(monthly_pdf.policy()),
    "monthly_policy_hash": fingerprint(monthly_facts.policy()),
    "current_policy_hash": fingerprint(monthly_current.policy()),
    "originals": "create-only content-addressed private archive before parsing; verify immutable generation and exact SHA-256; retain full original even on parser failure",
    "archive_bucket": ARCHIVE_BUCKET,
    "archive_prefix": ARCHIVE_PREFIX,
    "catalogs": "immutable original landing/catalog bytes with exact source URL and hashes, not just parsed links",
    "publication": "one complete two-variant source cohort plus a sealed run/publication receipt in one transaction; provisional snapshot inserts alone cannot change the core",
    "out_of_order": "current selection uses disclosed date per field, never fetch time; old archives cannot roll back current heads",
    "replay": "immutable execution ID for receipt replay; unchanged book adds a check receipt, not duplicate source snapshots",
    "failure": "append a bounded safe failure receipt; preserve previous usable current facts and retained originals; never claim freshness on failed acquisition",
    "verification": "needs_verification remains separate from source publication and transport success; no fake human verification",
    "source_rows_changed": 0,
    "UI": False,
}


def policy():
    return deepcopy(_POLICY)
