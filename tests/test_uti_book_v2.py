"""Synthetic header recovery, never actual fund/variant coverage certification."""

from copy import deepcopy
import hashlib

import pytest

from mfdataindia.enrichment import amfi_summary_identity, uti_book, uti_book_v2 as book
from test_amfi_summary_identity import identity, xml, URL
from test_uti_book import extraction

TITLE = "UTI BANKING & FINANCIAL SERVICES FUND"
NAME = "UTI Banking & Financial Services Fund"


def fixture(*, title=TITLE):
    value = extraction()
    page = value["pages"][1]
    page["text"] = page["text"].replace("UTI FLEXI CAP FUND", TITLE + "Sectoral")
    page["text_hash"] = hashlib.sha256(page["text"].encode()).hexdigest()
    page["physical_title"] = {"text": title, "text_hash": hashlib.sha256(title.encode()).hexdigest(),
                              "x": 15, "y": 756, "font_points": 16}
    page["physical_geometry"] = {"page_width": 594, "page_height": 792}
    rows = amfi_summary_identity.rows(xml().replace(b"Kotak Flexicap Fund", NAME.encode().replace(b"&", b"&amp;")), URL, "a" * 64)
    return value, {**identity(), "amc": book.AMC, "scheme_name": "not used as an issuer alias"}, rows


def derive(value, fund, bridges):
    details = book.detail_pages(value)
    for detail in details:
        detail["source_url"] = "https://source.invalid/synthetic-not-a-real-original.pdf"
    return book.derive(fund, bridges, details, [value], "2026-09-30")


def test_category_collision_resolved_by_original_span_not_string_repair():
    value, fund, bridges = fixture()
    before = deepcopy((value, fund, bridges))
    assert uti_book.detail_pages(value) == []
    result = derive(value, fund, bridges)
    assert result["status"] == "usable"
    assert result["fields"]["aum"]["value"] == "22495.18"
    assert result["reported_total_expense_ratio"]["value"] == "1.68"
    assert result["fields"]["base_expense_ratio"]["value"] is None
    assert result["detail_header_evidence"]["text"] == TITLE
    assert (value, fund, bridges) == before


@pytest.mark.parametrize("defect", ["missing_title", "missing_column", "missing_manager", "wrong_title", "mixed_case", "inferred_former", "unsupported_plan"])
def test_no_fallback_identity_guess_or_blanket_gap_rescue(defect):
    value, fund, bridges = fixture()
    page = value["pages"][1]
    if defect == "missing_title":
        page["physical_title"] = None
    elif defect == "missing_column":
        page["physical_left_text"] = None
    elif defect == "missing_manager":
        page["physical_left_text"] = page["physical_left_text"].replace("Fund Manager", "Unlabelled names")
    elif defect == "wrong_title":
        page["physical_title"]["text"] = "UTI OTHER FUND"
    elif defect == "mixed_case":
        page["physical_title"]["text"] = NAME
    elif defect == "inferred_former":
        page["physical_title"]["text"] = "UTI UNRELATED FUND (Erstwhile UTI Banking & Financial Services Fund)"
    elif defect == "unsupported_plan":
        fund["plan"] = "DIRECT"
    if page["physical_title"]:
        page["physical_title"]["text_hash"] = hashlib.sha256(page["physical_title"]["text"].encode()).hexdigest()
    assert derive(value, fund, bridges)["status"] == "gap"


def test_conflicted_issuer_code_remains_a_conflict_despite_good_header():
    value, fund, bridges = fixture()
    content = xml("Regular Plan-Growth-112090 Direct Plan-G-112090", "Regular Plan-Growth-INF174K01336")
    content = content.replace(b"Kotak Flexicap Fund", NAME.encode().replace(b"&", b"&amp;"))
    bridges = amfi_summary_identity.rows(content, URL, "a" * 64)
    assert derive(value, fund, bridges)["reason"] == "issuer_variant_identity_conflict"


def test_v1_detections_fields_and_explicit_former_aliases_are_unchanged():
    value = extraction()
    value["pages"][1]["text"] = value["pages"][1]["text"].replace("UTI FLEXI CAP FUND", "UTI FLEXI CAP FUND (Erstwhile UTI Equity Fund)")
    assert book.detail_pages(value) == uti_book.detail_pages(value)
    detail = book.detail_pages(value)[0]
    detail["source_url"] = "https://source.invalid/synthetic-not-a-real-original.pdf"
    assert book.fields(detail, value, "2026-09-30") == uti_book.fields(detail, value, "2026-09-30")


def test_multiple_source_details_are_not_resolved_by_current_database_name():
    value, fund, bridges = fixture()
    other = deepcopy(value["pages"][1])
    other["page"] = 22
    value["pages"].append(other)
    assert derive(value, fund, bridges)["reason"] == "monthly_heading_missing_or_ambiguous"


def test_tampered_title_hash_is_not_an_accepted_original_span():
    value, fund, bridges = fixture()
    value["pages"][1]["physical_title"]["text_hash"] = "f" * 64
    with pytest.raises(ValueError, match="title hash"):
        derive(value, fund, bridges)


def test_original_typographic_apostrophe_is_not_a_database_alias():
    value, fund, _ = fixture(title="UTI CHILDREN’S EQUITY FUND")
    content = xml().replace(b"Kotak Flexicap Fund", b"UTI Children's Equity Fund")
    bridges = amfi_summary_identity.rows(content, URL, "a" * 64)
    assert derive(value, fund, bridges)["status"] == "usable"
    wrong = xml().replace(b"Kotak Flexicap Fund", b"UTI Children's Different Equity Fund")
    assert derive(value, fund, amfi_summary_identity.rows(wrong, URL, "a" * 64))["status"] == "gap"


def test_TER_only_does_not_become_primary_usable_but_keeps_title_provenance(monkeypatch):
    value, fund, bridges = fixture()
    original = uti_book.fields

    def supplemental_only(detail, extraction, as_of):
        values, ter, issues = original(detail, extraction, as_of)
        return {key: uti_book.missing(as_of, "synthetic_unavailable") for key in values}, ter, issues

    monkeypatch.setattr(uti_book, "fields", supplemental_only)
    result = derive(value, fund, bridges)
    assert result["status"] == "gap" and result["reason"] == "monthly_fields_unavailable"
    assert result["reported_total_expense_ratio"]["status"] == "accepted"
    assert result["detail_header_evidence"]["text"] == TITLE


def test_policy_is_a_revision_of_fields_not_a_new_financial_method():
    assert book.policy()["version"] == 2
    assert book.policy()["financial_field_rules_changed"] is False
    assert book.policy()["adjusted_idcw_analytics_enabled"] is False
    assert book.fields is uti_book.fields and book.normalize is uti_book.normalize
