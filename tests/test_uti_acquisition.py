"""Synthetic public catalogue boundary tests. No source network requests."""

from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from mfdataindia.enrichment import uti_acquisition as engine

URL = "https://www.utimf.com/api/get-fact-sheet?year=2026&month=October"


def rows():
    result = []
    for kind in ("Active", "Passive"):
        url = (
            "https://d3ce1o48hc5oli.cloudfront.net/utimf-job/ebook/UTI Fund Watch ("
            + kind
            + ")-October 2026.pdf"
        )
        result.append(
            {
                "name": "UTI Fund Watch (" + kind + ")-October 2026",
                "url": url,
                "doc": url,
                "type": "pdf",
                "category": "Fact Sheet",
                "month": "October",
                "year": "2026",
            }
        )
    result.extend([{**deepcopy(r), "name": r["name"] + " Hindi"} for r in result])
    return result


def test_english_is_selected_at_boundary_not_translated_or_defaulted():
    result = engine.catalogue_books(json.dumps({"rows": rows()}).encode(), URL)
    assert len(result) == 2 and [r["kind"] for r in result] == ["active", "passive"]
    assert all(
        r["language"] == "English" and "%20" in r["source_url"] and " " in r["issuer_url"]
        for r in result
    )
    assert all(engine.classify(r["source_url"])[0] == "book" for r in result)


@pytest.mark.parametrize(
    "defect",
    [
        "Hindi_only",
        "duplicate_English",
        "different_host",
        "Hindi_path",
        "wrong_month",
        "wrong_kind",
        "wrong_query",
        "auth_host",
        "doc_disagreement",
    ],
)
def test_no_ambiguous_language_identity_origin_or_query_fallback(defect):
    value = rows()
    if defect == "Hindi_only":
        value = value[2:]
    elif defect == "duplicate_English":
        value.append(deepcopy(value[0]))
    else:
        row = value[0]
        if defect == "different_host":
            row["url"] = row["url"].replace("d3ce1o48hc5oli", "other")
        elif defect == "Hindi_path":
            row["url"] = row["url"].replace(".pdf", " Hindi.pdf")
        elif defect == "wrong_month":
            row["url"] = row["url"].replace("October", "September")
        elif defect == "wrong_kind":
            row["url"] = row["url"].replace("(Active)", "(Passive)")
        elif defect == "wrong_query":
            row["url"] += "?token=private"
        elif defect == "auth_host":
            row["url"] = row["url"].replace("https://", "https://user@")
        elif defect == "doc_disagreement":
            row["doc"] = "https://attacker.invalid/a.pdf"
        if defect != "doc_disagreement":
            row["doc"] = row["url"]
    with pytest.raises(ValueError):
        engine.catalogue_books(json.dumps({"rows": value}).encode(), URL)


def test_versioned_public_english_path_is_preserved_exactly():
    url = "https://d3ce1o48hc5oli.cloudfront.net/s3fs-public/2026-09/uti_fund_watch_active_september_2026_rv2.pdf?VersionId=reX2dDqRa5Q_zBMJ60LJMR6hhVeDI2Lr"
    assert engine.requested_url(url, kind="Active", month="September", year="2026") == url
    with pytest.raises(ValueError):
        engine.requested_url(
            url + "&signature=unknown", kind="Active", month="September", year="2026"
        )


def test_edition_requests_are_not_the_source_reporting_dates():
    found = engine.recent_catalogues(datetime(2026, 1, 10, tzinfo=timezone.utc))
    assert found[0]["source_url"].endswith("year=2026&month=January")
    assert found[0]["expected_period"] == "2025-12-31"
    assert found[1]["source_url"].endswith("year=2025&month=December")
    assert found[1]["expected_period"] == "2025-11-30"
