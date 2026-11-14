"""Exact disjoint source projections and append-only receipt validation."""

from datetime import date
from types import SimpleNamespace

import pytest

from mfdataindia.review.policy import fingerprint
from mfdataindia.tagging import policy, source_evidence


def receipt(**changes):
    document = source_evidence._receipt_document(
        source_kind="full-catalogue",
        source_snapshot_run_id=41,
        catalogue_observed_on=date(2026, 11, 14),
        observed_on=date(2026, 11, 14),
        catalogue_census_hash="a" * 64,
        family_projection_hash="b" * 64,
    )
    publication_id = fingerprint(document)
    row = {
        "publication_sequence": 1,
        "publication_id": publication_id,
        "source_kind": "full-catalogue",
        "source_snapshot_run_id": 41,
        "catalogue_observed_on": date(2026, 11, 14),
        "observed_on": date(2026, 11, 14),
        "catalogue_census_hash": "a" * 64,
        "family_projection_hash": "b" * 64,
        "receipt_content_hash": publication_id,
    }
    row.update(changes)
    return row


def test_receipt_validation_rejects_missing_tampered_or_reverse_dated_evidence():
    assert source_evidence.validate(receipt())["publication_sequence"] == 1
    for value in (
        None,
        receipt(receipt_content_hash="0" * 64),
        receipt(observed_on=date(2026, 11, 13)),
    ):
        with pytest.raises(policy.TaggingError):
            source_evidence.validate(value)


def test_observations_hash_catalogue_and_family_as_disjoint_exact_projections():
    catalogue = [
        {
            "code": 1,
            "amc_id": 2,
            "amc": "AMC",
            "scheme_name": "Fund",
            "scheme_type": "Open",
            "scheme_category": "Equity Scheme - Large Cap Fund",
            "category_source": "AMFI",
            "plan_type": "DIRECT",
            "plan_source": "COLUMN",
            "option_type": "GROWTH",
            "is_etf": False,
            "in_scope": True,
            "last_seen": "2026-11-14",
        }
    ]
    families = [{"code": 1, "group_key": None, "family_id": None}]

    class Connection:
        def execute(self, statement):
            rows = catalogue if statement == source_evidence.CATALOGUE_SQL else families
            return SimpleNamespace(fetchall=lambda: rows)

    hashes = source_evidence.observations(Connection())
    assert hashes == {
        "catalogue_census_hash": policy.digest(catalogue),
        "family_projection_hash": policy.digest(families),
    }
    assert hashes["catalogue_census_hash"] != hashes["family_projection_hash"]
