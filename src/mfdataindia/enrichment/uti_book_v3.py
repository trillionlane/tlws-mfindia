"""Unchanged original title decisions and facts, canonical sealed coordinates."""
from mfdataindia.enrichment import uti_book_v2 as previous, uti_header_evidence
from mfdataindia.review.policy import fingerprint

KEY, AMC, DIRECTORY, CATALOGUE, CDN = previous.KEY, previous.AMC, previous.DIRECTORY, previous.CATALOGUE, previous.CDN
FIELDS, normalize = previous.FIELDS, previous.normalize
labelled_dates, period = previous.labelled_dates, previous.period
missing, fields, detail_pages = previous.missing, previous.fields, previous.detail_pages


def policy():
    return {**previous.policy(), "version": 3,
            "retained_heading_policy_hash": fingerprint(previous.policy()),
            "header_artifact_policy": uti_header_evidence.policy(),
            "measurement_decoder_changed": False, "financial_field_rules_changed": False}


def derive(identity, bridges, details, extractions, as_of):
    result = previous.derive(identity, bridges, details, extractions, as_of)
    if "detail_header_evidence" in result:
        return {**result, "detail_header_evidence": uti_header_evidence.encode(result["detail_header_evidence"])}
    return result
