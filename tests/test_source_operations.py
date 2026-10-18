"""Operational triage safety, not real-source or coverage acceptance."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
import pytest

from mfdataindia.enrichment import book_facts, book_store, source_operations as ops
from mfdataindia.review.policy import fingerprint
from test_amc_book_facts import prepared, publish
from test_verified_review_api import runtime_dsn
from test_verified_review_store import source_state
from test_verified_review_store import verified_db as verified_db

NOW = datetime(2026, 10, 18, 8, tzinfo=timezone.utc)


def sealed(body, key):
    return {**body, key: fingerprint(body)}


def fixture(reason="issuer_code_isin_bridge_missing"):
    identities = [{"code": code, "amc": "Synthetic", "scheme_name": "Fixture",
                   "plan": "REGULAR", "option": "GROWTH", "isins": []} for code in (1, 2)]
    rows = [{"identity": i, "result": {
        "status": "usable" if i["code"] == 1 else "gap",
        "reason": None if i["code"] == 1 else reason,
        "fields": {"aum": {"status": "accepted" if i["code"] == 1 else "unavailable",
                             "reason": None if i["code"] == 1 else reason}},
    }} for i in identities]
    batch = sealed({"format": "official-amc-book-batch-v1", "source_key": "absl-expanded",
                    "disclosure_as_of": "2026-09-30", "population_hash": fingerprint(identities),
                    "observations": rows}, "batch_id")
    run = sealed({"source_key": "absl-expanded", "execution_id": "synthetic-run",
                  "status": "updated", "observed_at": "2026-10-18T07:00:00+00:00",
                  "population_hash": batch["population_hash"], "batch_ids": [batch["batch_id"]]}, "run_id")
    status = {"format": "official-amc-book-status-v1", "configuration_hash": "a" * 64,
              "sources": [{"source_key": "absl-expanded", "current_denominator": 2,
                           "current_population_hash": batch["population_hash"],
                           "latest_run": run, "latest_successful_run": run,
                           "population_matches_latest_success": True,
                           "periods": [book_facts.coverage(batch)]}]}
    return status, {batch["batch_id"]: batch}


def test_replay_population_field_denominators_and_preservation():
    status, batches = fixture()
    original = deepcopy((status, batches))
    a = ops.build_inventory(status, batches, as_of=NOW)
    assert a == ops.build_inventory(status, batches, as_of=NOW.isoformat())
    assert (status, batches) == original
    assert a["inventory_hash"] == fingerprint({k: v for k, v in a.items() if k != "inventory_hash"})
    assert a["primary_gap_case_count"] == 1
    case = a["primary_gap_cases"][0]
    assert case["code"] == 2 and case["triage_class"] == "exact_identity_evidence_required"
    assert case["automatic_resolution_permitted"] is False
    source = a["sources"][0]
    assert source["missing_recent_periods"] == ["2026-08-31"]
    assert source["latest_success"]["age_seconds"] == 3600
    assert source["periods"][0]["denominator"] == 2
    assert source["periods"][0]["field_gap_counts_separate_from_variant_gaps"] == {"aum": {"issuer_code_isin_bridge_missing": 1}}
    assert not a["source_correctness_certified"] and not a["cloud_scheduler_health_certified"]
    assert a["verification_status"] == "needs_verification"
    assert a["nav_dataset_version"] is None
    assert ops.build_inventory(status, batches, as_of=NOW, nav_dataset_version=28)["nav_dataset_version"] == 28


@pytest.mark.parametrize("reason,category", [
    ("issuer_family_identity_conflict", "identity_conflict_review"),
    ("issuer_variant_identity_conflict", "identity_conflict_review"),
    ("monthly_heading_missing_or_ambiguous", "layout_or_source_heading_review"),
    ("unsupported_current_identity", "unsupported_identity_review"),
    ("monthly_fields_unavailable", "field_evidence_review"),
    ("new_source_reason", "unclassified_requires_review"),
    (None, "unclassified_requires_review"),
])
def test_reasons_are_diagnostic_not_automatic_corrections(reason, category):
    status, batches = fixture(reason)
    case = ops.build_inventory(status, batches, as_of=NOW)["primary_gap_cases"][0]
    assert case["triage_class"] == category
    assert case["automatic_resolution_permitted"] is False


def test_failure_keeps_prior_success_gaps_and_unknown_cloud_health():
    status, batches = fixture()
    source = status["sources"][0]
    source["latest_run"] = sealed({"source_key": "absl-expanded", "status": "failed",
                                  "execution_id": "new-failed-run", "observed_at": NOW.isoformat()}, "run_id")
    result = ops.build_inventory(status, batches, as_of=NOW)
    assert "latest_source_attempt_failed" in result["sources"][0]["alerts"]
    assert result["sources"][0]["latest_success"]["execution_id"] == "synthetic-run"
    assert result["primary_gap_case_count"] == 1


def test_failure_metadata_is_allowlisted_not_arbitrary_exception_text():
    status, batches = fixture()
    source = status["sources"][0]
    source["latest_run"] = sealed({"source_key": "absl-expanded", "status": "failed",
                                  "execution_id": "new-failed-run", "observed_at": NOW.isoformat(),
                                  "phase": "acquisition", "error_type": "TimeoutError",
                                  "message": "SECRET_MUST_NOT_BE_RETURNED"}, "run_id")
    failed = ops.build_inventory(status, batches, as_of=NOW)["sources"][0]["latest_attempt"]
    assert failed["failure_type"] == "TimeoutError" and failed["failure_phase"] == "acquisition"
    assert "SECRET_MUST_NOT_BE_RETURNED" not in str(failed)


def test_no_attempt_is_unknown_not_zero_coverage():
    status, _ = fixture()
    source = status["sources"][0]
    source.update(latest_run=None, latest_successful_run=None, periods=[], population_matches_latest_success=False)
    result = ops.build_inventory(status, {}, as_of=NOW)["sources"][0]
    assert result["current_denominator"] == 2 and result["periods"] == []
    assert "no_successful_source_evidence" in result["alerts"]


@pytest.mark.parametrize("defect", ["batch_seal", "run_seal", "missing_batch", "coverage", "duplicate_source",
                                  "population_flag", "denominator", "duplicate_batch", "future_attempt"])
def test_inconsistent_evidence_cannot_produce_plausible_inventory(defect):
    status, batches = fixture()
    source = status["sources"][0]
    if defect == "batch_seal":
        next(iter(batches.values()))["observations"][0]["identity"]["code"] = 3
    elif defect == "run_seal":
        source["latest_run"]["execution_id"] = "wrong"
    elif defect == "missing_batch":
        batches.clear()
    elif defect == "coverage":
        source["periods"][0]["denominator"] = 1
    elif defect == "duplicate_source":
        status["sources"].append(deepcopy(source))
    elif defect == "population_flag":
        source["population_matches_latest_success"] = False
    elif defect == "denominator":
        source["current_denominator"] = 1
    elif defect == "duplicate_batch":
        run = source["latest_successful_run"]
        run["batch_ids"] *= 2
        run["run_id"] = fingerprint({k: v for k, v in run.items() if k != "run_id"})
    else:
        run = source["latest_run"]
        run["observed_at"] = "2026-10-19T00:00:00Z"
        run["run_id"] = fingerprint({k: v for k, v in run.items() if k != "run_id"})
    with pytest.raises(ValueError):
        ops.build_inventory(status, batches, as_of=NOW)


@pytest.mark.parametrize("day", ["2026-10-18", "2026-10-18T08:00:00"])
def test_naive_operation_time_rejected(day):
    with pytest.raises(ValueError, match="timezone"):
        ops.instant(day)


def test_stored_policy_exact_bytes_and_calendar_boundaries():
    root = Path(__file__).resolve().parents[1]
    assert hashlib.sha256((root / "config" / ops.POLICY_FILE).read_bytes()).hexdigest() == ops.POLICY_SHA256
    assert ops.policy()["automatic_resolution_permitted"] is False
    assert ops.recent_periods(datetime(2024, 3, 1, tzinfo=timezone.utc)) == ["2024-02-29", "2024-01-31"]
    assert ops.recent_periods(datetime(2026, 1, 1, tzinfo=timezone.utc)) == ["2025-12-31", "2025-11-30"]


def rebind(status, batch):
    batch = sealed({k: v for k, v in batch.items() if k != "batch_id"}, "batch_id")
    source = status["sources"][0]
    run = dict(source["latest_successful_run"])
    run.update(source_key=batch["source_key"], batch_ids=[batch["batch_id"]], population_hash=batch["population_hash"])
    run = sealed({k: v for k, v in run.items() if k != "run_id"}, "run_id")
    source.update(source_key=batch["source_key"], latest_run=run, latest_successful_run=run,
                  periods=[book_facts.coverage(batch)], current_population_hash=batch["population_hash"])
    return {batch["batch_id"]: batch}


@pytest.mark.parametrize("expired", [False, True])
def test_known_document_expiry_does_not_erase_existing_evidence(expired):
    status, batches = fixture()
    batch = next(iter(batches.values()))
    batch.update(source_key="kotak-expanded", expected_recent_reporting_periods=["2026-09-30", "2026-08-31"],
                 single_fund_historical_disclosure=False, source_acquisition={
                     "mode": "reviewed_known_document_not_live_catalogue", "valid_before": "2026-11-01",
                     "latest_catalogue_discovery_available": False})
    batches = rebind(status, batch)
    result = ops.build_inventory(status, batches, as_of=datetime(2026, 11, 1, tzinfo=timezone.utc) if expired else NOW)
    source = result["sources"][0]
    assert "known_document_not_live_discovery" in source["alerts"]
    assert ("known_document_authority_expired" in source["alerts"]) is expired
    assert source["periods"][0]["statuses"]["usable"] == 1
    assert result["primary_gap_case_count"] == 1


def test_TER_supplemental_counts_are_not_BER_or_additional_primary_gaps():
    status, batches = fixture()
    batch = next(iter(batches.values()))
    batch["source_key"] = "uti-expanded"
    for row in batch["observations"]:
        row["result"].update(reported_total_expense_ratio={"status": "accepted" if row["identity"]["code"] == 1 else "unavailable",
                                                          "reason": "not_disclosed"}, date_discrepancies=[])
    result = ops.build_inventory(status, rebind(status, batch), as_of=NOW)
    period = result["sources"][0]["periods"][0]
    assert period["supplemental_accepted_fields"] == {"reported_total_expense_ratio": 1}
    assert "base_expense_ratio" not in period["accepted_fields"]
    assert period["field_gap_counts_separate_from_variant_gaps"]["reported_total_expense_ratio"] == {"not_disclosed": 1}
    assert result["primary_gap_case_count"] == 1 and not result["adjusted_idcw_analytics_enabled"]


def test_population_drift_preserves_last_success_without_claiming_current_coverage():
    status, batches = fixture()
    source = status["sources"][0]
    source.update(current_population_hash="f" * 64, current_denominator=3, population_matches_latest_success=False)
    result = ops.build_inventory(status, batches, as_of=NOW)
    assert "current_identity_population_not_certified_by_latest_success" in result["sources"][0]["alerts"]
    assert result["primary_gap_cases"][0]["population_matches_latest_success"] is False
    assert result["sources"][0]["periods"][0]["denominator"] == 2
    assert result["sources"][0]["current_denominator"] == 3


def test_policy_drift_is_not_silently_relabelled_version_one(monkeypatch):
    original = Path.read_bytes

    def changed(path):
        raw = original(path)
        return raw.replace(b'108000', b'108001') if path.name == ops.POLICY_FILE else raw

    monkeypatch.setattr(Path, "read_bytes", changed)
    with pytest.raises(ValueError, match="immutable"):
        ops.policy()


@pytest.mark.postgres
def test_actual_restricted_inventory_keeps_source_and_all_book_rows(verified_db, monkeypatch):
    identities, inputs, _ = prepared(verified_db, monkeypatch)
    publish(verified_db, identities, inputs)
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        original, before = source_state(conn), book_store.capture(conn)
    with psycopg.connect(runtime_dsn(verified_db, "mfdata_verified_reader"), row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ,READ ONLY")
        report = ops.inventory(conn, as_of=NOW)
        assert report["sources"][0]["current_denominator"] == len(identities)
        assert report["source_rows_changed"] == 0 and report["human_events_created"] == 0
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        assert source_state(conn) == original and book_store.capture(conn) == before


@pytest.mark.postgres
@pytest.mark.parametrize("mode", ["read_write", "read_committed", "owner"])
def test_actual_wrong_transaction_or_effective_identity_denied(verified_db, mode):
    role = None if mode == "owner" else "mfdata_verified_reader"
    with psycopg.connect(runtime_dsn(verified_db, role) if role else verified_db, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL " + ("READ COMMITTED" if mode == "read_committed" else "REPEATABLE READ")
                     + (",READ ONLY" if mode != "read_write" else ",READ WRITE"))
        with pytest.raises(ValueError, match="read-only|repeatable-read|restricted"):
            ops.inventory(conn, as_of=NOW)
