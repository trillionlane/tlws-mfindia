"""Two actual reporting periods, four English books, complete retained cohort."""

from datetime import date
import hashlib

from mfdataindia.enrichment import book_sources, uti_acquisition, uti_book_v2 as book, uti_book_pdf_v2
from mfdataindia.review.policy import fingerprint


def compile_batch(identities, monthly, bridges, originals, *, as_of, directory_hash):
    spec = book_sources.specification(book.KEY)
    if (
        not 1 <= len(identities) <= spec["maximum_current_variants"]
        or identities != sorted(identities, key=lambda i: i["code"])
        or len({i["code"] for i in identities}) != len(identities)
        or any(i["amc"] != book.AMC for i in identities)
    ):
        raise ValueError("complete bounded ordered UTI population required")
    if len(monthly) != 2 or {r["kind"] for r in monthly} != {"active", "passive"}:
        raise ValueError("both English active/passive books required")
    retained = {hashlib.sha256(o["content"]).hexdigest(): o for o in originals}
    pdfs = {
        key
        for key, o in retained.items()
        if o["role"] == "book" and o["media_type"] == "application/pdf"
    }
    if (
        len(pdfs) != 4
        or len(originals) > 512
        or not any(
            o["role"] == "directory" and o["source_url"] == book.DIRECTORY and key == directory_hash
            for key, o in retained.items()
        )
    ):
        raise ValueError("four actual retained PDFs and actual AMFI directory required")
    if not bridges or any(
        r["source_hash"] not in retained or r["index_hash"] not in retained for r in bridges
    ):
        raise ValueError("retained exact XML identity and parent indexes required")
    extractions = []
    details = []
    edges = []
    for record in sorted(monthly, key=lambda r: r["kind"]):
        extraction = record["extraction"]
        if (
            extraction["policy_hash"] != fingerprint(uti_book_pdf_v2.policy())
            or extraction["engine_hash"] != spec["decoder_engine_hash"]
            or extraction["extraction_hash"]
            != fingerprint({k: v for k, v in extraction.items() if k != "extraction_hash"})
        ):
            raise ValueError("reviewed physical-column extraction required")
        raw = retained.get(extraction["source_hash"])
        if not raw or raw["role"] != "book" or raw["source_url"] != record["source_url"]:
            raise ValueError("extraction must bind actual retained PDF URL and bytes")
        catalog = retained.get(record["catalogue_hash"])
        if not catalog or catalog["role"] != "catalogue":
            raise ValueError("actual English catalogue original required")
        selected = uti_acquisition.catalogue_books(catalog["content"], catalog["source_url"])
        if not any(
            r["source_url"] == record["source_url"]
            and r["issuer_url"] == record["issuer_url"]
            and r["kind"] == record["kind"]
            for r in selected
        ):
            raise ValueError("English language, kind and exact parent edge required")
        pages = book.detail_pages(extraction)
        if len(pages) < spec["minimum_detail_pages"][record["kind"]]:
            raise ValueError("reviewed source layout absent; blanket gap publication forbidden")
        details.extend({**d, "source_url": record["source_url"]} for d in pages)
        extractions.append(extraction)
        edges.append(
            {
                "role": record["kind"] + "_book",
                "language": "English",
                "content_hash": extraction["source_hash"],
                "source_url": record["source_url"],
                "issuer_url": record["issuer_url"],
                "catalogue_hash": record["catalogue_hash"],
                "extraction_hash": extraction["extraction_hash"],
                "decoder_policy_hash": extraction["policy_hash"],
                "decoder_engine_hash": extraction["engine_hash"],
            }
        )
    dates = {
        d
        for extraction in extractions
        for page in extraction["pages"]
        for d in book.labelled_dates(page["text"], "Portfolio as on")
    }
    if dates != {as_of}:
        raise ValueError("actual shared reporting date differs; never use edition month")
    observations = [
        {"identity": i, "result": book.derive(i, bridges, details, extractions, as_of)}
        for i in identities
    ]
    if not any(o["result"]["status"] == "usable" for o in observations):
        raise ValueError("no usable exact identity evidence; retain prior publication")
    primary = next(e for e in edges if e["role"] == "active_book")
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
    body = {
        "format": "official-amc-book-batch-v2",
        "source_key": book.KEY,
        "specification_hash": fingerprint(spec),
        "population_hash": fingerprint(identities),
        "content_hash": primary["content_hash"],
        "source_url": primary["source_url"],
        "identity_content_hash": directory_hash,
        "identity_content_role": "actual_amfi_directory_json_not_pdf",
        "disclosure_as_of": date.fromisoformat(as_of).isoformat(),
        "document_refs": refs,
        "monthly_extractions": edges,
        "decoder_policy_hash": fingerprint(uti_book_pdf_v2.policy()),
        "extraction_hash": fingerprint(edges),
        "observations": observations,
        "verification_status": "needs_verification",
        "source_rows_changed": 0,
        "canonical_analytics_replaced": False,
        "whole_factsheet_semantic_coverage": False,
        "adjusted_idcw_analytics_enabled": False,
        "language_selected": "English",
        "date_authority": "actual_Portfolio_as_on_not_edition_filename_or_URL",
        "expense_basis": "TER_separate_not_BER",
    }
    return {**body, "batch_id": fingerprint(body)}
