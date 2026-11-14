"""Exact disjoint source projections and append-only receipt validation."""

from datetime import date
from types import SimpleNamespace

import pytest

from mfdataindia.review.policy import fingerprint
from mfdataindia.tagging import policy, source_evidence


def receipt(**changes):
    document = source_evidence._receipt_document(
        predecessor_publication_id=None,
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
        "predecessor_publication_id": None,
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


def test_same_day_family_a_b_a_is_an_ordered_chain_but_identical_reentry_is_a_noop(
    monkeypatch,
):
    states = iter(
        [
            {"catalogue_census_hash": "a" * 64, "family_projection_hash": "1" * 64},
            {"catalogue_census_hash": "a" * 64, "family_projection_hash": "2" * 64},
            {"catalogue_census_hash": "a" * 64, "family_projection_hash": "1" * 64},
            {"catalogue_census_hash": "a" * 64, "family_projection_hash": "1" * 64},
        ]
    )
    monkeypatch.setattr(source_evidence, "observations", lambda conn: next(states))

    class Result:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class Connection:
        def __init__(self):
            self.rows = []

        def execute(self, statement, parameters=None):
            if statement.lstrip().startswith("SELECT"):
                return Result(self.rows[-1] if self.rows else None)
            (
                publication_id,
                predecessor_id,
                source_kind,
                snapshot_run_id,
                catalogue_observed_on,
                observed_on,
                catalogue_hash,
                family_hash,
                content_hash,
            ) = parameters
            row = {
                "publication_sequence": len(self.rows) + 1,
                "publication_id": publication_id,
                "predecessor_publication_id": predecessor_id,
                "source_kind": source_kind,
                "source_snapshot_run_id": snapshot_run_id,
                "catalogue_observed_on": catalogue_observed_on,
                "observed_on": observed_on,
                "catalogue_census_hash": catalogue_hash,
                "family_projection_hash": family_hash,
                "receipt_content_hash": content_hash,
            }
            self.rows.append(row)
            return Result({"publication_sequence": row["publication_sequence"]})

    conn = Connection()
    results = [
        source_evidence.append(
            conn,
            source_kind="fund-family-rebuild",
            source_snapshot_run_id=41,
            catalogue_observed_on=date(2026, 11, 14),
            observed_on=date(2026, 11, 14),
        )
        for _ in range(4)
    ]
    first_a, b, second_a, replay = results
    assert [row["publication_sequence"] for row in (first_a, b, second_a)] == [1, 2, 3]
    assert first_a["publication_id"] != second_a["publication_id"]
    assert b["predecessor_publication_id"] == first_a["publication_id"]
    assert second_a["predecessor_publication_id"] == b["publication_id"]
    assert replay == second_a
    assert len(conn.rows) == 3
