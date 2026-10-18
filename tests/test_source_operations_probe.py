import base64
from datetime import datetime, timezone
import json

import pytest

from mfdataindia.enrichment import kotak_acquisition, kotak_book
from mfdataindia.enrichment.source_operations_probe import probe

NOW = datetime(2026, 10, 18, tzinfo=timezone.utc)


@pytest.mark.parametrize("mode", ["valid", "redirect_failure", "malformed", "wrong_edge", "oversized"])
def test_exact_two_reads_retained_evidence_and_no_retry_or_fallback(mode):
    calls = []

    def fetch(url):
        calls.append(url)
        if mode == "redirect_failure":
            raise ValueError("not retained in result; no response body or secret leaked")
        month = "September" if "month=9&" in url else "August"
        raw = json.dumps({"status": "Success", "statusCode": "200", "subHeaderList": [{
            "headerId": 219, "contentType": "upload",
            "content": f"FormsDownloads/Factsheet/Factsheet-for-{month}-2026/KotakMFFactsheet{month}2026.pdf",
        }]}).encode()
        if mode == "malformed":
            raw = b"original successful but not a valid catalogue"
        elif mode == "oversized":
            raw = b"x" * 1048577
        return {"source_url": url, "final_url": url + ("wrong" if mode == "wrong_edge" else ""),
                "content": raw, "media_type": "application/json"}

    result = probe(NOW, transport=fetch)
    assert calls == [r["source_url"] for r in kotak_acquisition.recent_catalogues(NOW.date())]
    assert len(calls) == len(set(calls)) == 2
    for record in result["requests"]:
        assert record["catalogue_discovery_available"] is (mode == "valid")
        assert not record["fallback_used"] and not record["book_acquired"]
        assert record["retries"] == record["redirects_followed"] == 0
        assert record["original_retained"] is (mode in {"valid", "malformed"})
        if record["original_retained"]:
            assert len(base64.b64decode(record["original_base64"], validate=True)) == record["original_bytes"]
        if mode == "valid":
            assert record["candidate_book_url"].startswith(kotak_book.BOOK_PREFIX)
    assert not result["source_admission_certified"] and not result["known_document_authority_extended"]
    assert "no response body or secret" not in json.dumps(result)
