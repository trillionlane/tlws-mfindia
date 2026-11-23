"""SBI v2 batches keep month-end identity separate from exact PDF date."""

import calendar
import hashlib
from datetime import date

from mfdataindia.enrichment import book_sources, sbi_book_v2 as book
from mfdataindia.review.policy import fingerprint


def compile_batch(
    identities,
    monthly,
    bridges,
    originals,
    *,
    reporting_period,
    document_dates,
    directory_hash,
):
    spec = book_sources.specification(book.KEY)
    period = date.fromisoformat(reporting_period)
    if (
        spec["adapter"] != "sbi-full-book-v2"
        or period.day != calendar.monthrange(period.year, period.month)[1]
        or set(document_dates) != {"active", "passive"}
        or len({item.get("document_as_of") for item in document_dates.values()}) != 1
        or not 1 <= len(identities) <= spec["maximum_current_variants"]
        or identities != sorted(identities, key=lambda item: item["code"])
        or len({item["code"] for item in identities}) != len(identities)
        or any(item["amc"] != spec["amc"] for item in identities)
        or set(monthly) != {"active", "passive"}
        or not bridges
    ):
        raise ValueError("complete bounded SBI v2 evidence required")
    document_as_of = document_dates["active"]["document_as_of"]
    source_day = date.fromisoformat(document_as_of)
    if (source_day.year, source_day.month) != (period.year, period.month):
        raise ValueError("SBI document date outside reporting month")

    refs = sorted(
        {
            fingerprint(
                {
                    "content_hash": hashlib.sha256(original["content"]).hexdigest(),
                    "source_url": original["source_url"],
                    "media_type": original["media_type"],
                    "role": original["role"],
                }
            ): {
                "content_hash": hashlib.sha256(original["content"]).hexdigest(),
                "source_url": original["source_url"],
                "media_type": original["media_type"],
                "role": original["role"],
            }
            for original in originals
        }.values(),
        key=lambda item: (item["role"], item["source_url"], item["content_hash"]),
    )
    if (
        not 7 <= len(refs) <= 512
        or len({item["content_hash"] for item in refs if item["role"] == "book"}) != 4
    ):
        raise ValueError("all four actual books and bounded original graph required")
    if not any(
        item["role"] == "directory"
        and item["content_hash"] == directory_hash
        and item["source_url"] == book.DIRECTORY
        for item in refs
    ):
        raise ValueError("actual AMFI directory original required")

    details, extractions = [], []
    for role in ("active", "passive"):
        item = monthly[role]
        extraction = item["extraction"]
        selected = book.detail_pages(extraction)
        if len(selected) < spec["minimum_detail_pages_per_book"]:
            raise ValueError("SBI book layout differs; blanket gap publication forbidden")
        if not any(
            ref["role"] == "book"
            and ref["content_hash"] == extraction["source_hash"]
            and ref["source_url"] == item["source_url"]
            for ref in refs
        ):
            raise ValueError("monthly extraction must reference retained original")
        details.extend({**detail, "source_url": item["source_url"]} for detail in selected)
        date_evidence = document_dates[role]
        extractions.append(
            {
                "role": role,
                "content_hash": extraction["source_hash"],
                "source_url": item["source_url"],
                "extraction_hash": extraction["extraction_hash"],
                "decoder_policy_hash": extraction["policy_hash"],
                "document_as_of": date_evidence["document_as_of"],
                "document_date_evidence": {
                    key: date_evidence[key] for key in ("page", "text_hash", "source_anchor")
                },
            }
        )
    available = {item["content_hash"] for item in refs}
    if any(
        row["source_hash"] not in available or row["index_hash"] not in available for row in bridges
    ):
        raise ValueError("identity bridge original/index not retained")
    observations = [
        {
            "identity": identity,
            "result": book.derive(identity, bridges, details, reporting_period, document_as_of),
        }
        for identity in identities
    ]
    if not any(item["result"]["status"] == "usable" for item in observations):
        raise ValueError("no usable exact-identity monthly evidence")
    primary = extractions[0]
    body = {
        "format": "official-amc-book-batch-v3",
        "source_key": book.KEY,
        "specification_hash": fingerprint(spec),
        "population_hash": fingerprint(identities),
        "content_hash": primary["content_hash"],
        "source_url": primary["source_url"],
        "identity_content_hash": directory_hash,
        "identity_content_role": "actual_amfi_directory_json_not_pdf",
        "reporting_period": reporting_period,
        "disclosure_as_of": reporting_period,
        "document_as_of": document_as_of,
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
