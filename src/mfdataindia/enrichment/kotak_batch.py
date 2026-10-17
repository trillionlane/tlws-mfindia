"""Complete current population, actual PDF date and every identity-parent edge."""

import hashlib
from datetime import date

from mfdataindia.enrichment import book_sources, kotak_book as book, layout_book_pdf
from mfdataindia.review.policy import fingerprint


def compile_batch(
    identities, monthly, bridges, originals, *, as_of, directory_hash, expected_periods
):
    spec = book_sources.specification(book.KEY)
    if (
        not 1 <= len(identities) <= spec["maximum_current_variants"]
        or identities != sorted(identities, key=lambda i: i["code"])
        or len({i["code"] for i in identities}) != len(identities)
        or any(i["amc"] != book.AMC for i in identities)
        or not bridges
    ):
        raise ValueError(
            "complete bounded ordered Kotak population and actual identity evidence required"
        )
    extraction = monthly["extraction"]
    if (
        extraction["policy_hash"] != fingerprint(layout_book_pdf.policy("kotak"))
        or extraction["engine_hash"] != spec["decoder_engine_hash"]
        or extraction["extraction_hash"]
        != fingerprint(
            {key: value for key, value in extraction.items() if key != "extraction_hash"}
        )
    ):
        raise ValueError("reviewed physical-layout decoder required")
    refs = sorted(
        {
            fingerprint(r): r
            for r in [
                {
                    "content_hash": hashlib.sha256(o["content"]).hexdigest(),
                    "source_url": o["source_url"],
                    "media_type": o["media_type"],
                    "role": o["role"],
                }
                for o in originals
            ]
        }.values(),
        key=lambda r: (r["role"], r["source_url"], r["content_hash"]),
    )
    if (
        not 6 <= len(refs) <= 512
        or len({r["content_hash"] for r in refs if r["role"] == "book"}) != 2
    ):
        raise ValueError("two real retained PDF originals and bounded source graph required")
    if not any(
        r["role"] == "directory"
        and r["content_hash"] == directory_hash
        and r["source_url"] == book.DIRECTORY
        for r in refs
    ):
        raise ValueError("actual AMFI directory original required")
    if not any(
        r["role"] == "book"
        and r["content_hash"] == extraction["source_hash"]
        and r["source_url"] == monthly["source_url"]
        for r in refs
    ):
        raise ValueError("monthly extraction must bind an actual retained PDF")
    details = [{**r, "source_url": monthly["source_url"]} for r in book.detail_pages(extraction)]
    minimum = (
        1 if monthly["source_url"] == book.HISTORICAL else spec["minimum_current_detail_pages"]
    )
    if len(details) < minimum:
        raise ValueError("reviewed book layout absent; blanket gap publication forbidden")
    retained = {r["content_hash"] for r in refs}
    if any(r["source_hash"] not in retained or r["index_hash"] not in retained for r in bridges):
        raise ValueError("actual identity original and parent index required")
    if book.reporting_dates(extraction["pages"][0]["text"]) != {as_of}:
        raise ValueError("actual source date, not URL date, required")
    observations = [
        {"identity": i, "result": book.derive(i, bridges, details, as_of)} for i in identities
    ]
    if not any(r["result"]["status"] == "usable" for r in observations):
        raise ValueError("no usable exact-identity evidence; retain prior publication")
    monthly_extractions = [
        {
            "role": "whole_book"
            if monthly["source_url"] != book.HISTORICAL
            else "reviewed_historical_one_pager",
            "content_hash": extraction["source_hash"],
            "source_url": monthly["source_url"],
            "extraction_hash": extraction["extraction_hash"],
            "decoder_policy_hash": extraction["policy_hash"],
            "decoder_engine_hash": extraction["engine_hash"],
        }
    ]
    body = {
        "format": "official-amc-book-batch-v2",
        "source_key": book.KEY,
        "specification_hash": fingerprint(spec),
        "population_hash": fingerprint(identities),
        "content_hash": extraction["source_hash"],
        "source_url": monthly["source_url"],
        "identity_content_hash": directory_hash,
        "identity_content_role": "actual_amfi_directory_json_not_pdf",
        "disclosure_as_of": date.fromisoformat(as_of).isoformat(),
        "document_refs": refs,
        "monthly_extractions": monthly_extractions,
        "decoder_policy_hash": extraction["policy_hash"],
        "extraction_hash": fingerprint(monthly_extractions),
        "observations": observations,
        "verification_status": "needs_verification",
        "source_rows_changed": 0,
        "canonical_analytics_replaced": False,
        "whole_factsheet_semantic_coverage": False,
        "adjusted_idcw_analytics_enabled": False,
        "expected_recent_reporting_periods": expected_periods,
        "date_authority": "actual_source_text_not_filename_or_URL",
        "single_fund_historical_disclosure": monthly["source_url"] == book.HISTORICAL,
    }
    return {**body, "batch_id": fingerprint(body)}
