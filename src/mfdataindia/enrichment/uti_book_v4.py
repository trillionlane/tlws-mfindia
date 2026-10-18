"""Preserve every v3 fact; fill unavailable AUM only from a complete dated row."""

from decimal import Decimal

from mfdataindia.enrichment import monthly_facts, uti_aum_row, uti_book_v3 as previous, uti_book_pdf_v3
from mfdataindia.review.policy import fingerprint

KEY, AMC, DIRECTORY, CATALOGUE, CDN = previous.KEY, previous.AMC, previous.DIRECTORY, previous.CATALOGUE, previous.CDN
FIELDS, normalize = previous.FIELDS, previous.normalize
labelled_dates, period = previous.labelled_dates, previous.period
missing, fields, detail_pages = previous.missing, previous.fields, previous.detail_pages


def policy():
    return {**previous.policy(), "version": 4,
            "retained_field_parser_policy_hash": fingerprint(previous.policy()),
            "decoder_policy_hash": fingerprint(uti_book_pdf_v3.policy()),
            "additional_aum_row_policy": uti_aum_row.policy(),
            "financial_field_rules_changed": True,
            "replacement_of_prior_accepted_values": False}


def derive(identity, bridges, details, extractions, as_of):
    result = previous.derive(identity, bridges, details, extractions, as_of)
    if "detail_page" not in result or result["fields"]["aum"]["status"] == "accepted":
        return result
    matches = [d for d in details if d["page"]["page"] == result["detail_page"]
               and d["source_hash"] == result["detail_content_hash"]]
    if len(matches) != 1:
        raise ValueError("unique same-fund original AUM detail required")
    detail = matches[0]
    row = detail["page"].get("physical_aum_row")
    if (row is None or labelled_dates(detail["page"]["text"], "Portfolio as on") != {as_of}
            or labelled_dates(detail["left"], "Fund AUM/Folio as on") != {as_of}):
        return result
    uti_aum_row.validate(row)
    geometry = detail["page"].get("physical_geometry")
    if not geometry or any(row[k] != uti_aum_row.token(geometry[k]) for k in ("page_width", "page_height", "boundary_x")):
        raise ValueError("original AUM row region differs")
    raw = row["spans"][-2]["text"]
    value = monthly_facts.decimal(raw, high=Decimal("1000000000000"))
    field = {"status": "accepted", "value": value, "unit": "INR_crore", "scope": "fund", "reason": None,
             "disclosure_as_of": as_of, "field_effective_date": as_of, "date_basis": "explicit_field_date",
             "raw_value": raw, "source_anchor": "Closing AUM / complete original physical INR Crore row / explicit same-period Fund AUM/Folio as on",
             "content_hash": detail["source_hash"], "detail_page": detail["page"]["page"],
             "detail_text_hash": detail["page"]["text_hash"], "source_url": detail["source_url"]}
    return {**result, "status": "usable", "reason": None, "fields": {**result["fields"], "aum": field},
            "detail_aum_row_evidence": {"source_hash": detail["source_hash"], "detail_page": detail["page"]["page"],
                                        "row": row, "row_hash": fingerprint(row)}}
