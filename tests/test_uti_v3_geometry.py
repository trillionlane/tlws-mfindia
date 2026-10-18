"""Actual fractional geometry boundary, not issuer or rollout certification."""
from copy import deepcopy
from decimal import localcontext
import hashlib
import json
from pathlib import Path

import psycopg
import pytest

from mfdataindia.enrichment import book_sources, uti_header_evidence as codec, uti_runtime_v3
from mfdataindia.review.policy import fingerprint


def header():
    title = "UTI TRANSPORTATION AND LOGISTICS FUND"
    return {"method": "unique_original_physical_title", "source_hash": "475e80f437abab2c66817059b258997a0f358e73fa6e2940c8e52d066eaa981a",
            "text": title, "text_hash": hashlib.sha256(title.encode()).hexdigest(),
            "x": 15.624, "y": 756.5192, "font_points": 18.0,
            "page_width": 594.0, "page_height": 792.0}


def test_actual_measurements_exact_strings_no_rounding_or_ambient_context():
    original = header()
    expected = {**original, "x": "15.624", "y": "756.5192", "font_points": "18.0",
                "page_width": "594.0", "page_height": "792.0"}
    for precision in (1, 6, 28, 100):
        with localcontext() as context:
            context.prec = precision
            assert codec.encode(original) == expected
    assert original == header()
    zero = {**original, "x": -0.0}
    assert codec.encode(zero)["x"] == "0"
    assert codec.encode({**original, "x": 15})["x"] == "15"


@pytest.mark.parametrize("value", [True, "15.624", None, float("nan"), float("inf"), -1, 60, 1e-19])
def test_reject_coercion_nonfinite_unbounded_or_outside_title_region(value):
    with pytest.raises(ValueError):
        codec.encode({**header(), "x": value})


@pytest.mark.parametrize("field,value", [("font_points", 31), ("y", 700), ("page_width", 2001), ("page_height", 0)])
def test_same_frozen_physical_bounds(field, value):
    with pytest.raises(ValueError):
        codec.encode({**header(), field: value})


def test_exact_keys_and_no_global_or_financial_rule_change():
    with pytest.raises(ValueError):
        codec.encode({**header(), "extra": "not admitted"})
    assert codec.policy()["global_canonicalizer_changed"] is False
    assert codec.policy()["financial_values_changed"] is False
    assert book_sources.current_policy()["version"] == 10
    archived = json.loads((Path(__file__).resolve().parents[1] / "config/unaccepted/official-current-facts-policy-v9.json").read_text())
    assert fingerprint(archived) == "073eeaa85f714d817dc8debc86177f796bd9083f3b14a5bcc4b3f07cb2352f81"
    assert fingerprint(book_sources.current_policy(4)) == "2b1d478498a72a267ecd4719300a5456db52fa0764b2a446e9ee75b545839e5a"
    transition = book_sources.current_policy()["source_authority_transition"]
    assert transition["source_count_changed"] is False
    assert transition["financial_field_rules_changed"] is False


def test_database_diagnostics_are_allowlisted_never_arbitrary_body():
    for error in (ValueError("password secret DSN row"), psycopg.Error("password secret DSN row")):
        assert uti_runtime_v3.safe_database_diagnostic(error) == {}
    error = psycopg.errors.RaiseException("password secret DSN row")
    result = uti_runtime_v3.safe_database_diagnostic(error)
    assert result == {"database_diagnostic": {"sqlstate": "P0001", "guard": "database_rejection_no_body"}}
    assert "secret" not in json.dumps(result)


def test_geometry_does_not_change_other_evidence():
    original = header()
    encoded = codec.encode(deepcopy(original))
    assert {k: v for k, v in encoded.items() if k not in codec.FIELDS} == {k: v for k, v in original.items() if k not in codec.FIELDS}


def test_real_isolated_worker_fractional_header_to_canonical_artifact():
    from mfdataindia.enrichment import uti_book_pdf_v2
    from test_uti_book_pdf_v2 import real_fixture
    result = uti_book_pdf_v2.extract(real_fixture(fractional=True))
    page = result["pages"][0]
    artifact = {"method": "unique_original_physical_title", "source_hash": result["source_hash"],
                **page["physical_title"], **{k: page["physical_geometry"][k] for k in ("page_width", "page_height")}}
    encoded = codec.encode(artifact)
    assert encoded["x"] == "15.624" and encoded["y"] == "756.5192"
    assert encoded["font_points"] == "18.0"
    assert all(type(encoded[k]) is str for k in codec.FIELDS)


def test_versioned_derive_preserves_every_financial_and_identity_field():
    from mfdataindia.enrichment import uti_book_v2, uti_book_v3
    from test_uti_book_v2 import fixture
    value, fund, bridges = fixture()
    value["pages"][1]["physical_title"].update(x=15.624, y=756.5192, font_points=18.0)
    value["pages"][1]["physical_geometry"] = {"page_width": 594.0, "page_height": 792.0}
    details = uti_book_v2.detail_pages(value)
    for detail in details:
        detail["source_url"] = "https://source.invalid/synthetic-not-a-real-original.pdf"
    previous = uti_book_v2.derive(fund, bridges, details, [value], "2026-09-30")
    current = uti_book_v3.derive(fund, bridges, details, [value], "2026-09-30")
    assert current == {**previous, "detail_header_evidence": codec.encode(previous["detail_header_evidence"])}
    assert current["status"] == "usable" and current["fields"]["base_expense_ratio"]["value"] is None
