"""Real subprocess and hermetic provider protocol tests; no live DB or provider."""

import io
import json
import subprocess

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from mfdataindia.enrichment import acquisition, pdf_evidence
from mfdataindia.enrichment.evidence import public_url
from mfdataindia.enrichment.source_capture import extract_document
from mfdataindia.ingest.amc_factsheets.download import (
    BASE, AbSLFactsheetClient, monthly_api_url, monthly_documents,
)
from test_official_enrichment_transport import Response, transport

HTML = "var factsheetDatasourceId = '{2D97ABF5-4F12-479A-86D6-27C538F48D13}';"
LINK = "/-/media/bsl/files/resources/factsheets/2026/september.pdf"


def data(code="1", link=LINK, title="Empower for the Month of September 2026"):
    return {"ReturnCode": code, "MonthlyFactsheets": [{"DocumentTitle": title, "DocumentLink": link}]}


@pytest.mark.parametrize("code", ["0", 0, "1", 1])
def test_observed_success_variants(code):
    documents = monthly_documents(data(code), 2026)
    assert documents[0].period == "September 2026" and documents[0].url == BASE + LINK


def test_constructed_sitecore_query_is_transmitted_with_no_global_query_exception(monkeypatch):
    calls = transport(monkeypatch, [Response(json.dumps(data()).encode(), headers={"Content-Type": "application/json"})])
    result = acquisition.fetch_absl_monthly(HTML, 2026)
    assert monthly_documents(json.loads(result["content"]), 2026)
    assert calls[0]["path"].endswith("datasourceId=%7B2D97ABF5-4F12-479A-86D6-27C538F48D13%7D&year=2026")
    with pytest.raises(ValueError):
        public_url(monthly_api_url(HTML, 2026))


@pytest.mark.parametrize("destination", ["/different", "https://evil.example/api", "/api?year=2026,ENV=true"])
def test_query_adapter_rejects_redirect_before_next_socket(monkeypatch, destination):
    calls = transport(monkeypatch, [Response(status=302, headers={"Location": destination})])
    with pytest.raises(ValueError):
        acquisition.fetch_absl_monthly(HTML, 2026)
    assert len(calls) == 1


@pytest.mark.parametrize("html,year", [
    (HTML, True), (HTML, "2026"), (HTML, 1800), ("no identifier", 2026),
    (HTML + HTML.replace("2D97ABF5", "3D97ABF5"), 2026),
    ("var factsheetDatasourceId = '{abc}';", 2026),
])
def test_invalid_coordinates_never_connect(monkeypatch, html, year):
    calls = transport(monkeypatch, [])
    with pytest.raises(ValueError):
        acquisition.fetch_absl_monthly(html, year)
    assert calls == []


@pytest.mark.parametrize("payload", [
    data("2"), data(True), {"ReturnCode": 1}, data(title="Empower September 2026"),
    data(title="Empower for the Month of September 2025"),
    data(link="https://evil.example/fund.pdf"), data(link=LINK + "?key=value"),
    data(link="/other-document.pdf"), {"ReturnCode": 1, "MonthlyFactsheets": [1]},
    data(link=LINK.replace("september.pdf", "../other.pdf")),
    data(link=LINK.replace("september.pdf", "%2e%2e/other.pdf")),
    data(link=LINK.replace("september.pdf", "%252e%252e/other.pdf")),
    data(link=LINK.replace("september.pdf", "september.js")),
    {"ReturnCode": 1, "MonthlyFactsheets": data()["MonthlyFactsheets"] * 2},
])
def test_unsafe_or_ambiguous_document_list_fails_closed(payload):
    with pytest.raises(ValueError):
        monthly_documents(payload, 2026)


def test_period_sort_is_year_before_month():
    assert AbSLFactsheetClient._period_key("January 2026") > AbSLFactsheetClient._period_key("December 2025")


def test_pdf_adapter_adds_only_fixed_public_same_origin_referer(monkeypatch):
    calls = transport(monkeypatch, [Response(b"%PDF-fixture", headers={"Content-Type": "application/pdf"})])
    result = acquisition.fetch_absl_document(BASE + LINK)
    assert result["content"].startswith(b"%PDF-")
    assert calls[0]["headers"]["Referer"] == BASE + "/forms-and-downloads/factsheets"
    assert "Cookie" not in calls[0]["headers"] and "Authorization" not in calls[0]["headers"]
    with pytest.raises(ValueError):
        acquisition.fetch_absl_document("https://evil.example/factsheet.pdf")


@pytest.mark.parametrize("location", ["/other.pdf", LINK.replace("september.pdf", "../other.pdf"), "https://evil.example/x.pdf"])
def test_pdf_redirect_cannot_leave_approved_media_tree(monkeypatch, location):
    calls = transport(monkeypatch, [Response(status=302, headers={"Location": location})])
    with pytest.raises(ValueError):
        acquisition.fetch_absl_document(BASE + LINK)
    assert len(calls) == 1


def pdf(*, encrypted=False, pages=1, stream=None):
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=200, height=200)
        if stream is not None:
            obj = DecodedStreamObject()
            obj.set_data(stream)
            page[NameObject("/Contents")] = writer._add_object(obj.flate_encode())
    if encrypted:
        writer.encrypt("fixture-only")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_real_subprocess_blank_text_deterministic_and_not_accepted():
    raw = pdf()
    first = pdf_evidence.extract_pdf(raw)
    assert first == pdf_evidence.extract_pdf(raw)
    assert first["state"] == "no_text"
    assert first["pages"][0]["page"] == 1 and first["pages"][0]["text"] == ""
    assert first["facts_accepted"] is False and first["source_rows_changed"] == 0
    assert len(first["engine_code_hash"]) == 64


@pytest.mark.parametrize("raw", [b"not PDF", b"%PDF-broken", pdf(encrypted=True), pdf(pages=401), pdf(stream=b" " * 10000001)])
def test_invalid_encrypted_or_oversized_decoded_documents_rejected(raw):
    with pytest.raises(ValueError):
        pdf_evidence.extract_pdf(raw)


def test_deadline_failure_is_not_a_partial_success(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("fixture", 30)
    monkeypatch.setattr(pdf_evidence.subprocess, "run", timeout)
    with pytest.raises(ValueError, match="deadline"):
        pdf_evidence.extract_pdf(pdf())


def test_private_extraction_archive_is_replay_safe_and_bound_to_source(tmp_path):
    tmp_path.chmod(0o700)
    raw = pdf()
    original = tmp_path / "original.pdf"
    original.write_bytes(raw)
    sha = pdf_evidence.hashlib.sha256(raw).hexdigest()
    first = extract_document(tmp_path, original, sha)
    assert first == extract_document(tmp_path, original, sha)
    assert first["source_content_hash"] == sha and first["database_imported"] is False
    with pytest.raises(ValueError, match="hash"):
        extract_document(tmp_path, original, "0" * 64)
    assert original.read_bytes() == raw


def test_absl_bridge_emits_only_unmapped_candidate_not_float_facts():
    from pathlib import Path
    from mfdataindia.review.policy import fingerprint
    text = (Path(__file__).parent / "fixtures/absl/detail_bankpsu.txt").read_text()
    extracted = {"pages": [{"page": 17, "text": text}]}
    extracted["result_hash"] = fingerprint(extracted)
    result = pdf_evidence.absl_candidates(extracted, "September 2026")
    assert len(result) == 1
    assert result[0]["state"] == "unmapped_candidate" and not result[0]["facts_accepted"]
    assert result[0]["detail_page"] == 17
    assert "aum_crore" not in result[0] and "quant" not in result[0]
    extracted["pages"][0]["text"] = "altered"
    with pytest.raises(ValueError, match="seal"):
        pdf_evidence.absl_candidates(extracted, "September 2026")


def test_selected_pages_keep_original_numbers_and_explicit_incompleteness():
    result = pdf_evidence.extract_pdf(pdf(pages=5), page_start=3, page_end=4)
    assert [p["page"] for p in result["pages"]] == [3, 4]
    assert result["document_pages"] == 5 and not result["whole_document_text_extracted"]
    assert result["requested_page_range"] == [3, 4]
    with pytest.raises(ValueError):
        pdf_evidence.extract_pdf(pdf(), page_start=2)


@pytest.mark.parametrize("start,end", [(True, None), (0, 1), (2, 1), (1, 401), (1, False)])
def test_invalid_page_selection_is_rejected_before_worker(start, end):
    with pytest.raises(ValueError, match="selection"):
        pdf_evidence.extract_pdf(pdf(), page_start=start, page_end=end)
