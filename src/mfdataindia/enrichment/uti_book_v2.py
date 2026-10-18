"""Retain v1 facts; add unique original physical titles, never identity inference."""

import hashlib
import re

from mfdataindia.enrichment import uti_book, uti_book_pdf_v2
from mfdataindia.review.policy import fingerprint

KEY, AMC, DIRECTORY, CATALOGUE, CDN = (
    uti_book.KEY, uti_book.AMC, uti_book.DIRECTORY, uti_book.CATALOGUE, uti_book.CDN
)
FIELDS = uti_book.FIELDS
normalize = uti_book.normalize
labelled_dates = uti_book.labelled_dates
period = uti_book.period
missing = uti_book.missing
fields = uti_book.fields


def policy():
    return {**uti_book.policy(), "version": 2,
            "decoder_policy_hash": fingerprint(uti_book_pdf_v2.policy()),
            "retained_field_parser_policy_hash": fingerprint(uti_book.policy()),
            "additional_headings": "retain every v1 detail detection; otherwise one original uppercase physical UTI FUND/ETF title plus the unchanged complete physical field column and exact issuer code/ISIN identity; no database-name alias, category suffix repair, new normalization or layout-text fallback",
            "physical_title_aliases": "none; only v1 source-authored Erstwhile aliases remain available",
            "financial_field_rules_changed": False}


def detail_pages(extraction):
    result = uti_book.detail_pages(extraction)
    known = {d["page"]["page"] for d in result}
    for page in extraction["pages"]:
        left, title = page.get("physical_left_text"), page.get("physical_title")
        if page["page"] in known or left is None or not title:
            continue
        text = title["text"]
        if title["text_hash"] != hashlib.sha256(text.encode()).hexdigest():
            raise ValueError("original physical title hash differs")
        if (not re.fullmatch(r"UTI [A-Z0-9 &'’()/+:-]{3,200}(?:FUND|ETF)", text)
                or not re.search(r"(?m)^" + uti_book.MANAGER_HEADER + r"\s*$", left)):
            continue
        geometry = page.get("physical_geometry")
        if not isinstance(geometry, dict) or not {"page_width", "page_height"} <= geometry.keys():
            raise ValueError("original physical title page geometry required")
        result.append({"name": normalize(text), "names": {normalize(text)}, "page": page,
                       "left": left, "source_hash": extraction["source_hash"],
                       "physical_header_evidence": {"method": "unique_original_physical_title",
                                                    "source_hash": extraction["source_hash"], **title,
                                                    "page_width": geometry["page_width"],
                                                    "page_height": geometry["page_height"]}})
    return sorted(result, key=lambda detail: detail["page"]["page"])


def derive(identity, bridges, details, extractions, as_of):
    # The frozen exact-code/ISIN conflict checks and all field/date/expense
    # calculations remain the actual implementation, not a copied alternative.
    result = uti_book.derive(identity, bridges, details, extractions, as_of)
    if "detail_page" in result:
        matches = [d for d in details if d["page"]["page"] == result["detail_page"]
                   and d["source_hash"] == result["detail_content_hash"]]
        if len(matches) != 1:
            raise ValueError("unique source detail provenance required")
        if "physical_header_evidence" in matches[0]:
            result["detail_header_evidence"] = matches[0]["physical_header_evidence"]
    return result
