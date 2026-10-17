"""V2 job-envelope shape proofs; real source/HTTP gates are tested separately."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from mfdataindia.enrichment import book_sources
from mfdataindia.enrichment.book_certification import expectation
from mfdataindia.review.policy import fingerprint


@pytest.fixture(autouse=True)
def frozen_sbi_certification_authority(monkeypatch):
    """This historical envelope certifies v2, not a newer AMC's scope."""
    monkeypatch.setattr(book_sources, "CURRENT_VERSION", 2)


def fixture():
    manifest = SimpleNamespace(
        seal="synthetic-not-DEV", cohort=[100001, 101763, 103173, 103174, 103215, 103216, 102272]
    )
    fields = {
        "benchmark_name": "Fixture index",
        "fund_managers": ["Fixture Manager"],
        "base_expense_ratio": "1.4",
        "aum": "12345",
        "risk_level": "Very High",
    }
    reviewed = [
        {
            "code": code,
            "batch_id": "c" * 64,
            "as_of": "2026-09-30",
            "values": fields,
            "source_values": {**fields, "base_expense_ratio": "1.40"},
        }
        for code in (103215, 103216, 102272)
    ]
    aliases = {
        alias
        for group in book_sources.current_policy()["supported_fields"].values()
        for alias in group
    }
    doc = {
        "reviewed": reviewed,
        "prior_core_values": {str(c): {a: None for a in aliases} for c in (103215, 103216, 102272)},
        "preserved_core_values": {
            str(c): {a: None for a in aliases} for c in (101763, 103173, 103174)
        },
        "execution_id": "tlws-mf-official-facts-refresh-dev-synthetic-cert",
        "sources": {
            "absl-expanded": {
                "denominator": 192,
                "population_hash": "e" * 64,
                "batch_ids": ["a" * 64, "b" * 64],
            },
            "sbi-expanded": {
                "denominator": 258,
                "population_hash": "f" * 64,
                "batch_ids": ["c" * 64, "d" * 64],
            },
        },
        "prior_projection": {
            "format": "official-prior-amc-projection-v1",
            "previous_source_version": 1,
            "codes": [100001, 101763, 103173, 103174],
            "hashes": {},
            "source_rows_changed": 0,
        },
        "original_oracles": [
            {
                "code": code,
                "batch_id": b * 64,
                "as_of": "2026-09-30" if b == "c" else "2026-08-31",
                "source_values": fields,
            }
            for b in ("c", "d")
            for code in (103215, 102272)
        ],
    }
    return manifest, deepcopy(doc)


def bound(manifest, doc):
    return {
        "AMC_BOOK_CERTIFICATION": json.dumps(doc),
        "AMC_BOOK_CERTIFICATION_HASH": fingerprint(
            {"manifest": manifest.seal, "certification": doc}
        ),
    }


def test_current_v2_envelope_requires_all_sources_raw_tokens_and_both_period_oracles():
    manifest, doc = fixture()
    assert expectation(manifest, bound(manifest, doc)) == doc


@pytest.mark.parametrize(
    "defect",
    [
        "old_envelope",
        "missing_raw_tokens",
        "empty_oracle",
        "missing_source",
        "one_period",
        "oracle_outside_cohort",
        "oracle_outside_batches",
        "no_prior_projection",
    ],
)
def test_v2_cannot_reuse_incomplete_old_certification_envelope(defect):
    manifest, doc = fixture()
    if defect == "old_envelope":
        doc.pop("original_oracles")
    elif defect == "missing_raw_tokens":
        doc["reviewed"][0].pop("source_values")
    elif defect == "empty_oracle":
        doc["original_oracles"][0]["source_values"] = {}
    elif defect == "missing_source":
        doc["sources"].pop("sbi-expanded")
    elif defect == "one_period":
        doc["sources"]["sbi-expanded"]["batch_ids"].pop()
    elif defect == "oracle_outside_cohort":
        doc["original_oracles"][0]["code"] = 999999
    elif defect == "oracle_outside_batches":
        doc["original_oracles"][0]["batch_id"] = "9" * 64
    else:
        doc.pop("prior_projection")
    with pytest.raises(ValueError):
        expectation(manifest, bound(manifest, doc))
