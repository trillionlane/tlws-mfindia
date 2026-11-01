import json

import psycopg
import pytest

from mfdataindia.review import broad_reader_direct_grant as grant
from mfdataindia.review import reader_execution_diagnostics as diagnostics


def error(label):
    try:
        raise psycopg.errors.InsufficientPrivilege("SYNTHETIC_SECRET_DO_NOT_EXPORT")
    except psycopg.Error:
        try:
            raise grant.GrantError(label) from None
        except grant.GrantError as value:
            return value


@pytest.mark.parametrize("label,state,effect", [
    ("PRECOMMIT_ABORTED_NO_RETRY", "GRANT_ROLLED_BACK_CONFIRMED", diagnostics.ROLLED_BACK),
    ("ABORT_UNCONFIRMED_NO_RETRY", "ROLLBACK_OR_TRANSPORT_UNCONFIRMED", diagnostics.UNKNOWN),
    ("WALL_ABORT_UNCONFIRMED_NO_FURTHER_IO", "ROLLBACK_OR_TRANSPORT_UNCONFIRMED", diagnostics.UNKNOWN),
    ("COMMIT_OUTCOME_UNKNOWN", "COMMIT_OUTCOME_UNKNOWN", diagnostics.UNKNOWN),
    ("COMMITTED_UNVERIFIED", "COMMITTED_UNVERIFIED", diagnostics.COMMITTED),
])
def test_dominant_outcome_preserves_earliest_safe_cause(label, state, effect):
    result = diagnostics.report(error(label), stage="grant", grant_started=True)
    assert result["state"] == state and result["grant_effect"] == effect
    assert result["safe_cause"] == "PERMISSION_DENIED"
    assert "SECRET" not in json.dumps(result)


@pytest.mark.parametrize("effect", [diagnostics.UNKNOWN, diagnostics.COMMITTED, diagnostics.VERIFIED])
def test_later_read_application_or_certificate_failure_cannot_erase_grant(effect):
    for stage in ("reader", "application", "certificate", "transport"):
        result = diagnostics.report(RuntimeError("SYNTHETIC_SECRET"), stage=stage, prior_effect=effect)
        assert result["grant_effect"] == effect
        assert result["consumer_acceptance"] == "PENDING_RO04"
        assert "SECRET" not in json.dumps(result)


def test_only_typed_engine_outcomes_can_declare_commit_and_local_is_not_worker():
    value = diagnostics.report(RuntimeError("COMMITTED_UNVERIFIED"), stage="validation", worker=False)
    assert value["state"] == "LOCAL_VALIDATION_FAILED_NO_DISPATCH"
    assert value["grant_effect"] == diagnostics.NO_GRANT
    worker = diagnostics.report(RuntimeError("SYNTHETIC_SECRET"), stage="validation")
    assert worker["state"] == "WORKER_VALIDATION_FAILED_BEFORE_CONNECT"


@pytest.mark.parametrize("failure,expected", [
    (psycopg.errors.InvalidPassword("SYNTHETIC_SECRET"), "AUTHENTICATION_DENIED"),
    (psycopg.errors.LockNotAvailable("SYNTHETIC_SECRET"), "LOCK_UNAVAILABLE"),
    (psycopg.errors.QueryCanceled("SYNTHETIC_SECRET"), "DATABASE_TIME_BUDGET_EXPIRED"),
    (diagnostics.source.SourceAuthorityError("SYNTHETIC_SECRET"), "SOURCE_BINDING_INVALID"),
    (diagnostics.views.ViewProfileError("SYNTHETIC_SECRET"), "SOURCE_VIEW_PROFILE_UNREVIEWED"),
    (diagnostics.closure.FunctionClosureError("SYNTHETIC_SECRET"), "FUNCTION_CLOSURE_UNREVIEWED"),
    (diagnostics.ExecutionEvidenceNotDelivered("SYNTHETIC_SECRET"), "CERTIFICATE_NOT_DELIVERED"),
])
def test_safe_typed_causes_cover_runtime_boundaries(failure, expected):
    result = diagnostics.report(failure, stage="certificate", prior_effect=diagnostics.COMMITTED)
    assert result["safe_cause"] == expected
    assert result["grant_effect"] == diagnostics.COMMITTED
    assert "SYNTHETIC_SECRET" not in json.dumps(result)


def test_post_submission_local_journal_failure_never_claims_no_dispatch():
    result = diagnostics.report(ValueError("SYNTHETIC_SECRET"), stage="journal",
        prior_effect=diagnostics.UNKNOWN, worker=False, dispatch_possible=True)
    assert result["state"] == "TRANSPORT_OUTCOME_UNCONFIRMED"
    assert result["grant_effect"] == diagnostics.UNKNOWN


@pytest.mark.parametrize("failure", [diagnostics.attestation.AbortUnconfirmed("SYNTHETIC_SECRET"),
    diagnostics.attestation.WallAbortUnconfirmed("SYNTHETIC_SECRET"),
    diagnostics.reader.ReaderCheckError("READER_ABORT_UNCONFIRMED_NO_RETRY"),
    diagnostics.reader.ReaderCheckError("READER_WALL_SPENT_NO_FURTHER_IO")])
def test_readonly_abort_is_explicit_without_erasing_a_preceding_commit(failure):
    capture = diagnostics.report(failure, stage="capture")
    assert capture["state"] == "ROLLBACK_OR_TRANSPORT_UNCONFIRMED"
    assert capture["grant_effect"] == diagnostics.NO_GRANT
    assert capture["safe_cause"] == "READ_ONLY_ROLLBACK_UNCONFIRMED"
    read = diagnostics.report(failure, stage="reader", prior_effect=diagnostics.VERIFIED)
    assert read["grant_effect"] == diagnostics.VERIFIED
    assert read["state"] == "COMMITTED_ACL_VERIFIED_LATER_CHECK_FAILED"
    assert "SYNTHETIC_SECRET" not in json.dumps(read)
