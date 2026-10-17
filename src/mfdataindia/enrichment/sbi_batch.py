"""One complete AMC denominator per period, with multiple real original PDFs."""

import calendar
import hashlib
from datetime import date

from mfdataindia.enrichment import book_sources, sbi_book
from mfdataindia.review.policy import fingerprint


def compile_batch(identities, monthly, bridges, originals, *, as_of, directory_hash):
    spec = book_sources.specification(sbi_book.KEY)
    day = date.fromisoformat(as_of)
    if (
        day.day != calendar.monthrange(day.year, day.month)[1]
        or not 1 <= len(identities) <= spec["maximum_current_variants"]
        or identities != sorted(identities, key=lambda i: i["code"])
        or len({i["code"] for i in identities}) != len(identities)
        or any(i["amc"] != spec["amc"] for i in identities)
        or set(monthly) != {"active", "passive"}
        or not bridges
    ):
        raise ValueError(
            "complete bounded SBI population, active/passive books and exact identity evidence required"
        )
    refs = []
    for original in originals:
        refs.append(
            {
                "content_hash": hashlib.sha256(original["content"]).hexdigest(),
                "source_url": original["source_url"],
                "media_type": original["media_type"],
                "role": original["role"],
            }
        )
    refs = sorted(
        {fingerprint(r): r for r in refs}.values(),
        key=lambda r: (r["role"], r["source_url"], r["content_hash"]),
    )
    if (
        not 7 <= len(refs) <= 512
        or len({r["content_hash"] for r in refs if r["role"] == "book"}) != 4
    ):
        raise ValueError("all four actual books and bounded original document graph required")
    if not any(
        r["role"] == "directory"
        and r["content_hash"] == directory_hash
        and r["source_url"] == sbi_book.DIRECTORY
        for r in refs
    ):
        raise ValueError("actual AMFI directory original required")
    details = []
    extractions = []
    for kind in ("active", "passive"):
        item = monthly[kind]
        extraction = item["extraction"]
        selected = sbi_book.detail_pages(extraction)
        if len(selected) < spec["minimum_detail_pages_per_book"]:
            raise ValueError("SBI book layout differs; blanket gap publication forbidden")
        if not any(
            r["role"] == "book"
            and r["content_hash"] == extraction["source_hash"]
            and r["source_url"] == item["source_url"]
            for r in refs
        ):
            raise ValueError("monthly extraction must reference actual retained original")
        details.extend({**detail, "source_url": item["source_url"]} for detail in selected)
        extractions.append(
            {
                "role": kind,
                "content_hash": extraction["source_hash"],
                "source_url": item["source_url"],
                "extraction_hash": extraction["extraction_hash"],
                "decoder_policy_hash": extraction["policy_hash"],
            }
        )
    available = {r["content_hash"] for r in refs}
    if any(r["source_hash"] not in available or r["index_hash"] not in available for r in bridges):
        raise ValueError("identity bridge original/index not retained")
    observations = [
        {"identity": i, "result": sbi_book.derive(i, bridges, details, as_of)} for i in identities
    ]
    if not any(r["result"]["status"] == "usable" for r in observations):
        raise ValueError(
            "no usable exact-identity monthly evidence; preserve last good publication"
        )
    primary = extractions[0]
    body = {
        "format": "official-amc-book-batch-v2",
        "source_key": sbi_book.KEY,
        "specification_hash": fingerprint(spec),
        "population_hash": fingerprint(identities),
        "content_hash": primary["content_hash"],
        "source_url": primary["source_url"],
        "identity_content_hash": directory_hash,
        "identity_content_role": "actual_amfi_directory_json_not_pdf",
        "disclosure_as_of": as_of,
        "document_refs": refs,
        "monthly_extractions": extractions,
        "decoder_policy_hash": primary["decoder_policy_hash"],
        "extraction_hash": fingerprint(extractions),
        "observations": observations,
        "verification_status": "needs_verification",
        "source_rows_changed": 0,
        "canonical_analytics_replaced": False,
        "whole_factsheet_semantic_coverage": False,
        "adjusted_idcw_analytics_enabled": False,
    }
    return {**body, "batch_id": fingerprint(body)}
