"""Retained-original and lifecycle tests, not live discovery/DEV acceptance."""

from datetime import datetime, timezone
import hashlib

import pytest

from mfdataindia.enrichment import (
    book_sources, kotak_acquisition as acquisition, kotak_known_document as known,
    kotak_runtime as runtime,
)
from mfdataindia.review.policy import fingerprint

NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)
REQUEST = {"source_url": known.CATALOGUE_URL, "expected_period": "2026-09-30"}


def test_actual_retained_original_is_byte_exact_and_names_reviewed_public_book():
    original = known.original(REQUEST, NOW)
    assert len(original["content"]) == 545
    assert hashlib.sha256(original["content"]).hexdigest() == known.CATALOGUE_SHA256
    assert "final_url" not in original  # No fabricated fresh transport response.
    assert acquisition.catalogue_book(original["content"], original["source_url"]) == (
        "https://vatseelabs-s3.kotakmf.com/FormsDownloads/Factsheet/"
        "Factsheet-for-September-2026/KotakMFFactsheetSeptember2026.pdf"
    )


@pytest.mark.parametrize("day", [(2026, 10, 16), (2026, 11, 1), (2027, 10, 17)])
def test_no_undated_or_open_ended_known_document(day):
    assert known.original(REQUEST, datetime(*day, tzinfo=timezone.utc)) is None


@pytest.mark.parametrize("period", ["2026-08-31", "2026-10-31", "2025-09-30"])
def test_no_other_period_can_use_september_original(period):
    assert known.original({**REQUEST, "expected_period": period}, NOW) is None


def test_explicit_aware_time_and_exact_endpoint_required():
    with pytest.raises(ValueError, match="explicit acquisition time"):
        known.original(REQUEST, datetime(2026, 10, 17))
    with pytest.raises(ValueError, match="exact retained catalogue"):
        known.original({**REQUEST, "source_url": known.CATALOGUE_URL + "&extra=true"}, NOW)


def test_modified_retained_bytes_fail_closed(monkeypatch):
    monkeypatch.setattr(known, "ORIGINAL_BASE64", known.ORIGINAL_BASE64[:-4] + "AAAA")
    with pytest.raises(ValueError, match="actual retained catalogue"):
        known.original(REQUEST, NOW)


@pytest.mark.parametrize("error", [ValueError("unavailable"), TimeoutError(), OSError()])
def test_acquisition_failure_has_one_call_and_explicit_non_live_provenance(error):
    calls = []
    def fetch(url):
        calls.append(url)
        raise error
    original, authority, issue = runtime.catalogue_input(REQUEST, NOW, fetch)
    assert calls == [known.CATALOGUE_URL]
    assert hashlib.sha256(original["content"]).hexdigest() == known.CATALOGUE_SHA256
    assert authority == known.authority()
    assert authority["latest_catalogue_discovery_available"] is False
    assert authority["expected_pdf_sha256"] == known.PDF_SHA256
    assert issue["latest_catalogue_discovery_available"] is False
    assert issue["reason"] == "ordinary_source_unavailable_reviewed_known_document_used"


def test_acquired_contradictory_catalogue_is_not_replaced(monkeypatch):
    def forbidden(*_):
        raise AssertionError("must not consult retained evidence after successful acquisition")
    monkeypatch.setattr(known, "original", forbidden)
    acquired = {"source_url": known.CATALOGUE_URL, "content": b'{"status":"different"}'}
    original, authority, issue = runtime.catalogue_input(REQUEST, NOW, lambda _: acquired)
    assert original is acquired and authority["mode"] == "live_catalogue" and issue is None
    with pytest.raises(ValueError):
        acquisition.catalogue_book(original["content"], original["source_url"])


def test_expired_acquisition_failure_is_not_suppressed():
    def fetch(_):
        raise TimeoutError("unavailable")
    with pytest.raises(TimeoutError):
        runtime.catalogue_input(REQUEST, datetime(2026, 11, 1, tzinfo=timezone.utc), fetch)


def test_changed_pdf_is_archived_but_never_decoded_or_published(monkeypatch):
    from test_book_original_retention import prepared
    _, run, events = prepared(monkeypatch, "kotak")
    def retained(request, observed, transport):
        return transport(request["source_url"]), known.authority(), {"unit_only": True}
    monkeypatch.setattr(runtime, "catalogue_input", retained)
    result = run()
    assert result["status"] == "failed" and result["phase"] == "books"
    assert len(result["archives"]) == 1
    assert [e for e, _ in events if e != "fetch"] == ["archive"]
    assert result["source_rows_changed"] == 0


def test_prior_accepted_source_policy_and_configuration_remain_frozen():
    assert fingerprint(book_sources.configuration(2)) == (
        "fa113f5e095fe5b7ad1e738401552d8ffbea15d44ebeb5e3fdf313680f6cf221"
    )
    assert fingerprint(book_sources.current_policy(2)) == (
        "151093a8798bb41803315b73bca944bb292adc661701173225173958d3b17302"
    )
    spec = book_sources.specification("kotak-expanded")
    assert spec["reviewed_known_document_policy"] == known.policy()
    assert "kotak_known_document" in spec["evidence_engine_hashes"]
