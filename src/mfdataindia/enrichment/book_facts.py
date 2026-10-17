"""Deterministic whole-AMC batches. Publication and human review are separate."""

import calendar
from collections import Counter
from datetime import date

from mfdataindia.enrichment import absl_expansion, book_sources
from mfdataindia.review.policy import fingerprint


def compile_batch(source_key, identities, monthly, summary, *, as_of, source_url):
    spec = book_sources.specification(source_key)
    day = date.fromisoformat(as_of)
    if (
        day.day != calendar.monthrange(day.year, day.month)[1]
        or not 1 <= len(identities) <= spec["maximum_current_variants"]
        or identities != sorted(identities, key=lambda i: i["code"])
        or len({i["code"] for i in identities}) != len(identities)
        or any(i["amc"] != spec["amc"] for i in identities)
    ):
        raise ValueError("bounded ordered current whole-AMC population required")
    if not source_url.startswith(spec["book_prefix"]) or not source_url.endswith(".pdf"):
        raise ValueError("registered official monthly book URL required")
    if summary["source_hash"] not in {s["sha256"] for s in spec["identity_documents"]}:
        raise ValueError("reviewed issuer identity original required")
    summaries = absl_expansion.summary_pages(summary)
    details = absl_expansion.detail_pages(monthly)
    if len(details) < spec["minimum_detail_pages"] or not summaries:
        raise ValueError("whole-book layout differs; no blanket gap publication")
    observations = []
    for identity in identities:
        result = absl_expansion.derive_one(identity, summaries, details, as_of=as_of)
        if not result["fields"]:
            result["fields"] = {
                field: {
                    "status": "unavailable",
                    "value": None,
                    "unit": None,
                    "scope": None,
                    "reason": result["reason"],
                    "disclosure_as_of": as_of,
                    "field_effective_date": None,
                    "date_basis": "no_usable_field_evidence",
                    "raw_value": None,
                    "source_anchor": None,
                }
                for field in absl_expansion.FIELDS
            }
        observations.append({"identity": identity, "result": result})
    body = {
        "format": "official-amc-book-batch-v1",
        "source_key": source_key,
        "specification_hash": fingerprint(spec),
        "population_hash": fingerprint(identities),
        "content_hash": monthly["source_hash"],
        "identity_content_hash": summary["source_hash"],
        "source_url": source_url,
        "disclosure_as_of": as_of,
        "decoder_policy_hash": monthly["policy_hash"],
        "extraction_hash": monthly["extraction_hash"],
        "observations": observations,
        "verification_status": "needs_verification",
        "source_rows_changed": 0,
        "canonical_analytics_replaced": False,
        "whole_factsheet_semantic_coverage": False,
        "adjusted_idcw_analytics_enabled": False,
    }
    return {**body, "batch_id": fingerprint(body)}


def coverage(batch):
    rows = batch["observations"]
    return {
        "batch_id": batch["batch_id"],
        "disclosure_as_of": batch["disclosure_as_of"],
        "population_hash": batch["population_hash"],
        "denominator": len(rows),
        "statuses": dict(Counter(r["result"]["status"] for r in rows)),
        "gap_reasons": dict(Counter(r["result"]["reason"] for r in rows if r["result"]["reason"])),
        "accepted_fields": dict(
            Counter(
                k
                for r in rows
                for k, v in r["result"]["fields"].items()
                if v["status"] == "accepted"
            )
        ),
        "verification_status": "needs_verification",
        "all_fields_complete": False,
    }
