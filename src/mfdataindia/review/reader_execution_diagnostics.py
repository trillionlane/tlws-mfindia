"""Allowlisted outcomes, never exception text, SQL, credentials or class names."""
import psycopg
import jwt

from . import broad_reader_direct_grant as grant
from . import broad_reader_direct_delivery_spec as spec
from . import broad_reader_policy as policy
from . import broad_reader_source_authority as source
from . import broad_reader_view_profile as views
from . import broad_reader_function_scope as functions
from . import broad_reader_dependency_scope as dependencies
from . import broad_reader_lock_witness as witness
from . import broad_reader_function_closure as closure
from . import broad_reader_live_snapshot as snapshot
from . import broad_reader_direct_plan as planner
from . import broad_reader_direct_capacity as capacity
from . import broad_reader_direct_reader_check as reader
from . import reader_function_attestation as attestation
from .evidence_transport import ExecutionEvidenceNotDelivered
from . import reader_access_profile as profiles
from .reader_execution_contract import ContractError

NO_GRANT = "NO_GRANT_SUBMITTED"
ROLLED_BACK = "ROLLED_BACK_CONFIRMED"
UNKNOWN = "MUTATION_OUTCOME_UNKNOWN"
COMMITTED = "COMMITTED_UNVERIFIED"
VERIFIED = "COMMITTED_ACL_VERIFIED"
EFFECTS = frozenset({NO_GRANT, ROLLED_BACK, UNKNOWN, COMMITTED, VERIFIED})
PHASES = frozenset({"validation", "preflight", "journal", "dsn", "connect", "capture",
                    "grant", "reader", "application", "certificate", "transport"})
_GRANT_LABELS = frozenset({"COMMIT_OUTCOME_UNKNOWN", "COMMITTED_UNVERIFIED",
                          "PRECOMMIT_ABORTED_NO_RETRY", "ABORT_UNCONFIRMED_NO_RETRY",
                          "WALL_ABORT_UNCONFIRMED_NO_FURTHER_IO", "OBSERVER_ABORT_UNCONFIRMED_NO_RETRY"})
_CAUSES = {"42501": "PERMISSION_DENIED", "28P01": "AUTHENTICATION_DENIED",
           "28000": "AUTHENTICATION_DENIED", "3D000": "DATABASE_IDENTITY_INVALID",
           "55P03": "LOCK_UNAVAILABLE", "57014": "DATABASE_TIME_BUDGET_EXPIRED",
           "08003": "CONNECTION_UNCONFIRMED", "08006": "CONNECTION_UNCONFIRMED",
           "57P01": "CONNECTION_UNCONFIRMED"}
_INPUT = frozenset({"DUPLICATE_MEMBER", "INVALID_CANONICAL_JSON", "NONCANONICAL_INPUT",
                    "OPERATION_GIT_MISMATCH", "OPERATION_CODE_MISMATCH", "OPERATION_WINDOW_EXPIRED", "OPERATION_WINDOW_INVALID",
                    "INCOMPLETE_PREDECESSOR_CHAIN", "STAGE_IDENTITY_OR_INTENT_MISMATCH",
                    "FIXED_INTERNAL_POLICY_REQUIRED", "EXPLICIT_DEV_NO_FREEZE_MODE_REQUIRED",
                    "PREDECESSOR_BINDING_MISMATCH", "COMPLETE_PROOF_CAPACITY_EXCEEDED",
                    "EXACT_OPERATION_PLAN_REQUIRED", "COMPLETE_SOURCE_BINDING_REQUIRED"})
_PREFLIGHT = frozenset({"CURRENT_BACKEND_INPUT_DRIFT", "EXACT_CURRENT_OPERATOR_REQUIRED",
    "EXISTING_OPERATOR_IAM_REQUIRED", "FIXED_TARGET_DRIFT", "PREDECESSOR_INVENTORY_DRIFT",
    "PREDECESSOR_JOB_DRIFT", "PREDECESSOR_CERTIFICATE_DRIFT", "CURRENT_TARGET_NOT_EMPTY",
    "CURRENT_IMAGE_BINDING_DRIFT", "PROTECTED_BASELINE_DRIFT", "ORIGINAL_REQUEST_DRIFT",
    "APPLICATION_TRUST_BINDING_MISMATCH", "APPLICATION_ASSERTION_SCOPE_MISMATCH",
    "INCOMPLETE_GRANT_PROOF", "INCOMPLETE_CAPTURE_PROOF", "UNVERIFIED_READER",
    "EXECUTION_STATUS_UNCONFIRMED", "TASK_RETRY_NOT_AUTHORIZED", "JOB_READINESS_FAILED",
    "JOB_READINESS_EXPIRED", "EXECUTION_INSPECTION_EXPIRED", "CURRENT_JOB_PROFILE_DRIFT"})
_INPUT = _INPUT | _PREFLIGHT
_TYPES = ((source.SourceAuthorityError, "SOURCE_BINDING_INVALID"),
          (jwt.PyJWTError, "DELEGATED_ASSERTION_INVALID_OR_EXPIRED"),
          (views.ViewProfileError, "SOURCE_VIEW_PROFILE_UNREVIEWED"),
          (functions.FunctionScopeError, "EXISTING_HELPER_NOT_ATTESTED"),
          (dependencies.DependencyScopeError, "DEPENDENCY_SCOPE_UNSUPPORTED"),
          (witness.LockWitnessError, "ACTUAL_LOCK_WITNESS_INVALID"),
          (closure.FunctionClosureError, "FUNCTION_CLOSURE_UNREVIEWED"),
          (snapshot.SnapshotError, "COMPLETE_CATALOGUE_PROOF_INVALID"),
          (planner.PlanError, "COMPLETE_GRANT_PLAN_INVALID"),
          (capacity.CapacityError, "COMPLETE_PROOF_CAPACITY_EXCEEDED"),
          (ExecutionEvidenceNotDelivered, "CERTIFICATE_NOT_DELIVERED"),
          (profiles.ReaderAccessError, "READER_POLICY_INVALID"))
STATES = frozenset({"COMMITTED_UNVERIFIED", "COMMITTED_ACL_VERIFIED_LATER_CHECK_FAILED",
    "COMMIT_OUTCOME_UNKNOWN", "ROLLBACK_OR_TRANSPORT_UNCONFIRMED", "GRANT_ROLLED_BACK_CONFIRMED",
    "WORKER_VALIDATION_FAILED_BEFORE_CONNECT", "LOCAL_VALIDATION_FAILED_NO_DISPATCH",
    "CAPTURE_FAILED_NO_GRANT", "AUTHENTICATED_READER_CHECK_FAILED",
    "APPLICATION_CHECKS_FAILED_CONSUMER_PENDING", "TRANSPORT_OUTCOME_UNCONFIRMED"})
CAUSES = frozenset(_CAUSES.values()) | _INPUT | frozenset(label for _, label in _TYPES) | frozenset({
    "CONFIGURATION_INVALID", "WHOLE_OPERATION_WALL_EXPIRED", "CONNECT_FAILED", "UNEXPECTED_FAILURE",
    "APPLICATION_GUARD_OR_READ_FAILED", "CERTIFICATE_INVALID", "READ_ONLY_ROLLBACK_UNCONFIRMED",
    "OPERATION_WINDOW_NOT_LIVE", "PREFLIGHT_OBSERVATION_UNCONFIRMED", "TRANSPORT_OR_DELIVERY_UNCONFIRMED"})


def _walk(error):
    pending, seen, result = [error], set(), []
    while pending and len(seen) < 32:
        item = pending.pop(0)
        if not isinstance(item, BaseException) or id(item) in seen:
            continue
        seen.add(id(item))
        result.append(item)
        pending.extend((item.__cause__, item.__context__))
        if isinstance(item, BaseExceptionGroup):
            pending.extend(item.exceptions[:32])
    return result


def report(error, *, stage, prior_effect=NO_GRANT, grant_started=False, worker=True,
           dispatch_possible=False):
    if stage not in PHASES or prior_effect not in EFFECTS:
        raise ValueError("INVALID_DIAGNOSTIC_CONTEXT")
    chain = _walk(error)
    labels = {item.args[0] for item in chain if isinstance(item, grant.GrantError)
              and len(item.args) == 1 and type(item.args[0]) is str
              and item.args[0] in _GRANT_LABELS}
    readonly_abort = any(isinstance(item, (attestation.AbortUnconfirmed, attestation.WallAbortUnconfirmed))
        or (isinstance(item, reader.ReaderCheckError) and len(item.args) == 1
            and item.args[0] in {"READER_ABORT_UNCONFIRMED_NO_RETRY", "READER_WALL_SPENT_NO_FURTHER_IO"})
        for item in chain)
    causes = [_CAUSES[item.sqlstate] for item in chain if isinstance(item, psycopg.Error)
              and item.sqlstate in _CAUSES]
    if not causes:
        causes = [item.args[0] for item in chain if isinstance(item, ContractError)
                  and len(item.args) == 1 and type(item.args[0]) is str and item.args[0] in _INPUT]
    if not causes:
        causes = [label for item in chain for error_type, label in _TYPES if isinstance(item, error_type)]
    if not causes:
        causes = ["OPERATION_WINDOW_NOT_LIVE" for item in chain if isinstance(item, policy.PolicyError)
                  and item.args == ("DIRECT_DEV_WINDOW_NOT_LIVE",)]
    cause = causes[-1] if causes else (
        "CONFIGURATION_INVALID" if any(isinstance(x, (ContractError, spec.DeliverySpecError)) for x in chain)
        else "WHOLE_OPERATION_WALL_EXPIRED" if any(isinstance(x, policy.WallExpired) for x in chain)
        else "READ_ONLY_ROLLBACK_UNCONFIRMED" if readonly_abort
        else "CONNECT_FAILED" if stage == "connect"
        else "APPLICATION_GUARD_OR_READ_FAILED" if stage == "application"
        else "CERTIFICATE_INVALID" if stage == "certificate"
        else "PREFLIGHT_OBSERVATION_UNCONFIRMED" if stage == "preflight"
        else "TRANSPORT_OR_DELIVERY_UNCONFIRMED" if stage == "transport" else "UNEXPECTED_FAILURE")
    effect = prior_effect
    if prior_effect in (COMMITTED, VERIFIED):
        state = "COMMITTED_UNVERIFIED" if prior_effect == COMMITTED else "COMMITTED_ACL_VERIFIED_LATER_CHECK_FAILED"
    elif "COMMIT_OUTCOME_UNKNOWN" in labels:
        state, effect = "COMMIT_OUTCOME_UNKNOWN", UNKNOWN
    elif "COMMITTED_UNVERIFIED" in labels:
        state, effect = "COMMITTED_UNVERIFIED", COMMITTED
    elif labels & {"ABORT_UNCONFIRMED_NO_RETRY", "WALL_ABORT_UNCONFIRMED_NO_FURTHER_IO",
                   "OBSERVER_ABORT_UNCONFIRMED_NO_RETRY"}:
        state = "ROLLBACK_OR_TRANSPORT_UNCONFIRMED"
        effect = UNKNOWN if grant_started else prior_effect
    elif readonly_abort:
        state = "ROLLBACK_OR_TRANSPORT_UNCONFIRMED"
    elif "PRECOMMIT_ABORTED_NO_RETRY" in labels and grant_started:
        state, effect = "GRANT_ROLLED_BACK_CONFIRMED", ROLLED_BACK
    elif grant_started:
        state, effect = "ROLLBACK_OR_TRANSPORT_UNCONFIRMED", UNKNOWN
    elif dispatch_possible:
        state = "TRANSPORT_OUTCOME_UNCONFIRMED"
    elif stage in {"validation", "preflight", "journal", "dsn"}:
        state = "WORKER_VALIDATION_FAILED_BEFORE_CONNECT" if worker else "LOCAL_VALIDATION_FAILED_NO_DISPATCH"
    elif stage == "capture":
        state = "CAPTURE_FAILED_NO_GRANT"
    elif stage == "reader":
        state = "AUTHENTICATED_READER_CHECK_FAILED"
    elif stage == "application":
        state = "APPLICATION_CHECKS_FAILED_CONSUMER_PENDING"
    else:
        state = "TRANSPORT_OUTCOME_UNCONFIRMED"
    # A failed reader or local check cannot erase an earlier ambiguous mutation.
    if prior_effect == UNKNOWN:
        effect = UNKNOWN
    return {"state": state, "reason": state, "safe_cause": cause, "stage": stage,
            "grant_effect": effect, "disposition": "STOP_INSPECT_ONLY_NO_REPLAY",
            "consumer_acceptance": "PENDING_RO04"}
