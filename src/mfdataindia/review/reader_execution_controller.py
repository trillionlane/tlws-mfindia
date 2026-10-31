"""One bounded plan, three deterministic stages; inspection cannot submit work.

Source seals and candidate hashes are not execution authority. RO-04 must
provide a directly approved complete plan. No authority is generated here.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import time

from . import broad_reader_direct_delivery_spec as spec
from . import broad_reader_direct_grant as direct
from . import broad_reader_direct_rollout_controller as old
from . import reader_execution_contract as contract
from . import reader_execution_diagnostics as diagnostics
from . import reader_execution_evidence as evidence
from .reader_execution_journal import Journal, ProgramLock
from .policy import fingerprint

require = contract.require


def recorded_request(prepared):
    require(prepared["body"] == contract.canonical(prepared["request"]).encode()
            and hashlib.sha256(prepared["body"]).hexdigest() == prepared["request_sha256"],
            "ORIGINAL_REQUEST_DRIFT")
    return {key: deepcopy(value) for key, value in prepared.items() if key != "body"}


def restored_request(value):
    result = deepcopy(value)
    result["body"] = contract.canonical(result["request"]).encode()
    require(hashlib.sha256(result["body"]).hexdigest() == result["request_sha256"]
            and len(result["body"]) == result["request_bytes"], "ORIGINAL_REQUEST_DRIFT")
    return result


def inspect_request(plan, kind, predecessors, stored):
    """Re-derive fixed commands/resources/payload, not just a self-hash."""
    recorded = restored_request(stored)
    pins = {row["path"]: row["sha256"] for row in plan["code_binding"]["files"]}
    rebuilt = spec.request(kind, root=contract.operation_root(plan), final_git_sha=plan["git_sha"],
        image=plan["image_binding"]["app"], payload=contract.stage_payload(plan, kind, predecessors),
        worker_sha256=pins["scripts/reader_execution_dev.py"],
        controller_sha256=pins["src/mfdataindia/review/reader_execution_controller.py"],
        spec_sha256=pins["src/mfdataindia/review/broad_reader_direct_delivery_spec.py"],
        source_sha256=fingerprint(plan["packaged_source"]), operation_id=plan["operation_id"])
    require(recorded == rebuilt, "ORIGINAL_REQUEST_DRIFT")
    return recorded


def validate_authority(plan, authority):
    require(type(authority) is dict and set(authority) == {
        "format", "plan_sha256", "operation_id", "approved_by", "stages", "window",
        "scope", "direct_human_approval_record_sha256"}
        and authority["format"] == "mf-reader-bounded-plan-authority-v1"
        and authority["plan_sha256"] == fingerprint(plan)
        and authority["operation_id"] == plan["operation_id"]
        and authority["approved_by"] == spec.OPERATOR
        and authority["stages"] == list(contract.KINDS)
        and authority["window"] == plan["window"]
        and authority["scope"] == "ONE_DEV_CAPTURE_DERIVED_GRANT_AUTHENTICATED_READER",
        "RO04_DIRECT_BOUNDED_PLAN_AUTHORITY_REQUIRED")
    spec.sha(authority["direct_human_approval_record_sha256"])


def terminal(value):
    """Accept failed terminal tasks for diagnostic delivery, never as success."""
    status = value.get("status")
    require(type(status) is dict and type(status.get("conditions")) is list,
            "EXECUTION_STATUS_UNCONFIRMED")
    rows = [r for r in status["conditions"] if type(r) is dict and r.get("type") == "Completed"]
    require(len(rows) == 1 and rows[0].get("status") in ("True", "False", "Unknown"),
            "EXECUTION_STATUS_UNCONFIRMED")
    for key in ("failedCount", "retriedCount", "cancelledCount", "runningCount", "succeededCount"):
        require(type(status.get(key, 0)) is int and status.get(key, 0) in (0, 1),
                "EXECUTION_STATUS_UNCONFIRMED")
    require(status.get("retriedCount", 0) == 0, "TASK_RETRY_NOT_AUTHORIZED")
    if rows[0]["status"] == "Unknown":
        return None
    require(status.get("runningCount", 0) == 0, "EXECUTION_STATUS_UNCONFIRMED")
    if rows[0]["status"] == "True":
        require(status.get("succeededCount") == 1
                and not status.get("failedCount", 0) and not status.get("cancelledCount", 0),
                "EXECUTION_STATUS_UNCONFIRMED")
        return True
    require(status.get("succeededCount", 0) == 0
            and status.get("failedCount", 0) + status.get("cancelledCount", 0) == 1,
            "EXECUTION_STATUS_UNCONFIRMED")
    return False


def _job(plan, kind, value, prepared, *, count, seal=None, identity=None):
    current = spec.validate_job_export(kind, value, prepared, namespace=spec.PROJECT_NUMBER,
        expected_executions=count, operation_id=plan["operation_id"])
    require(seal is None or current == seal, "CURRENT_JOB_PROFILE_DRIFT")
    old._job_status(value, execution=identity)
    return current


def _execution(plan, kind, value, prepared, seal, identity=None):
    return spec.validate_execution_export(kind, value, prepared, job=seal,
        image=plan["image_binding"]["app"], original_identity=identity,
        operation_id=plan["operation_id"])


def inventory(plan, kind, backend, prepared, seal, identity=None):
    job = backend.job(kind)
    _job(plan, kind, job, prepared, count=int(identity is not None), seal=seal, identity=identity)
    executions = backend.executions(kind)
    require(type(executions) is list and len(executions) == int(identity is not None),
            "CURRENT_EXECUTION_INVENTORY_DRIFT")
    if identity is not None:
        _execution(plan, kind, executions[0], prepared, seal, identity)
    return {"job_sha256": fingerprint(job), "execution_sha256": fingerprint(executions)}


def _verified(plan, kind, predecessors, certificate):
    body = evidence.read_certificate(plan, kind, predecessors, certificate)
    if body["state"] not in evidence.SUCCESS:
        require(body["data"] == {} and body["disposition"] == "STOP_INSPECT_ONLY_NO_REPLAY",
                "INVALID_FAILURE_CERTIFICATE")
        return body, False
    if kind == "capture":
        evidence.verify_chain(plan, "reader" if body["state"] == evidence.NOOP else "grant", [certificate])
    elif kind == "grant":
        captured = evidence.verify_chain(plan, "grant", predecessors)["capture"]
        evidence.verify_grant(plan, predecessors, body, captured)
    else:
        evidence.verify_reader(plan, predecessors, certificate)
    return body, True


def _unresolved(lock):
    # A different operation must never conceal unresolved prior mutation effects.
    for root in lock.roots():
        with Journal(root) as prior:
            prior.plan()
            rows = prior.events()
            require(rows and rows[-1]["state"] in {
                "OPERATION_APPLICATION_VERIFIED", "STOPPED_WITH_KNOWN_EFFECT", "INSPECTED_WITH_KNOWN_EFFECT"}
                and rows[-1]["grant_effect"] != diagnostics.UNKNOWN,
                "PRIOR_OPERATION_REQUIRES_READ_ONLY_INSPECTION")


def _stage(plan, kind, predecessors, backend, prepared, journal, *, effect, clock, sleep):
    direct.direct_live(plan["window"], remaining=30 if kind == "capture" else 20)
    # Complete intent durable BEFORE the first possible create submission.
    journal.append(state="CREATE_OUTCOME_UNKNOWN", kind=kind, effect=effect,
        data={"prepared": recorded_request(prepared), "predecessors": predecessors,
              "request_sha256": prepared["request_sha256"]})
    created = backend.create(kind, prepared["body"])
    seal = _job(plan, kind, created, prepared, count=0)
    journal.append(state="JOB_CREATED", kind=kind, effect=effect, data={"job_seal": seal})
    deadline = clock() + 300
    for _ in range(31):
        job = backend.job(kind)
        _job(plan, kind, job, prepared, count=0, seal=seal)
        ready = [c for c in job.get("status", {}).get("conditions", [])
                 if type(c) is dict and c.get("type") == "Ready"]
        require(len(ready) == 1 and ready[0].get("status") in ("Unknown", "True"), "JOB_READINESS_FAILED")
        if ready[0]["status"] == "True":
            break
        require(clock() < deadline, "JOB_READINESS_EXPIRED")
        direct.direct_live(plan["window"], remaining=30)
        sleep(10)
    else:
        raise contract.ContractError("JOB_READINESS_EXPIRED")
    require(fingerprint(backend.protected()) == plan["protected_sha256"], "PROTECTED_BASELINE_DRIFT")
    inventory(plan, kind, backend, prepared, seal)
    direct.direct_live(plan["window"], remaining=30 if kind == "capture" else 20)
    possible = diagnostics.UNKNOWN if kind == "grant" else effect
    journal.append(state="RUN_OUTCOME_UNKNOWN", kind=kind, effect=possible,
                   data={"job_seal": seal, "prepared_sha256": prepared["request_sha256"]})
    executed = backend.execute(kind)
    identity = _execution(plan, kind, executed, prepared, seal)
    journal.append(state="EXECUTION_IDENTIFIED", kind=kind, effect=possible,
                   data={"identity": identity})
    inventory(plan, kind, backend, prepared, seal, identity)
    deadline = clock() + 720
    for _ in range(73):
        observed = backend.execution(kind, identity["name"])
        _execution(plan, kind, observed, prepared, seal, identity)
        outcome = terminal(observed)
        if outcome is not None:
            break
        require(clock() < deadline, "EXECUTION_INSPECTION_EXPIRED")
        sleep(10)  # Bounded status inspection, never another task/run submission.
    else:
        raise contract.ContractError("EXECUTION_INSPECTION_EXPIRED")
    certificate = backend.certificate(kind, identity["name"])
    body, success = _verified(plan, kind, predecessors, certificate)
    journal.append(state="CERTIFICATE_OBSERVED", kind=kind, effect=body["grant_effect"],
        data={"certificate": certificate, "task_success": outcome})
    # A process failure never erases a sealed committed/unknown outcome.
    require(outcome is True and success, "STAGE_FAILED_STOP_NO_REPLAY")
    inventory(plan, kind, backend, prepared, seal, identity)
    require(fingerprint(backend.protected()) == plan["protected_sha256"], "PROTECTED_BASELINE_DRIFT")
    return {"prepared": prepared, "job_seal": seal, "identity": identity,
            "certificate": certificate, "certificate_sha256": fingerprint(certificate)}, body


def execute(plan, authority, application, *, source_root, expected_git, backend_factory,
            journal_base=spec.ARTIFACTS, clock=time.monotonic, sleep=time.sleep):
    checked = contract.validate_plan(plan, source_root=source_root, expected_git=expected_git)
    validate_authority(checked, authority)
    application.validate(checked)  # No provider, SQL, secret or identity spending.
    contract.dry_run(checked, source_root=source_root, expected_git=expected_git)
    direct.direct_live(checked["window"], remaining=30)
    require(Path(journal_base).resolve() == spec.ARTIFACTS, "FIXED_JOURNAL_BASE_REQUIRED")
    with ProgramLock(journal_base) as lock:
        _unresolved(lock)
        # Current target/images/protected checks happen BEFORE identity allocation.
        prepared = contract.prepare_request(checked, "capture", [], source_root=source_root, expected_git=expected_git)
        backend = backend_factory(checked, "capture", source_root, prepared)
        backend.preflight(checked, prepared, {})
        direct.direct_live(checked["window"], remaining=30)
        with lock.create(checked) as journal:
            journal.append(state="BOUNDED_PLAN_STARTED", data={"authority_sha256": fingerprint(authority)})
            completed, predecessors = {}, []
            effect, stage = diagnostics.NO_GRANT, "transport"
            try:
                for kind in contract.KINDS:
                    if kind == "grant" and predecessors[0]["evidence"]["state"] == evidence.NOOP:
                        journal.append(state="GRANT_NOT_REQUIRED", kind=kind, effect=effect)
                        continue
                    stage = "preflight"
                    prepared = contract.prepare_request(checked, kind, predecessors,
                        source_root=source_root, expected_git=expected_git)
                    if kind != "capture":
                        backend = backend_factory(checked, kind, source_root, prepared)
                        backend.preflight(checked, prepared, completed)
                    stage = "transport"
                    record, body = _stage(checked, kind, predecessors, backend, prepared, journal,
                        effect=effect, clock=clock, sleep=sleep)
                    effect = body["grant_effect"]
                    completed[kind] = record
                    predecessors.append(record["certificate"])
                    journal.append(state="STAGE_VERIFIED", kind=kind, effect=effect,
                                   data={"certificate_sha256": record["certificate_sha256"]})
                stage = "application"
                direct.direct_live(checked["window"], remaining=15)
                result = application.check(checked)
                direct.direct_live(checked["window"])
                require(result["state"] == "APPLICATION_CHECKS_VERIFIED_CONSUMER_PENDING"
                    and result["consumer_acceptance"] == "PENDING_RO04", "APPLICATION_PROOF_REQUIRED")
                # Final inventory THEN protected last, after actual API checks.
                for kind, record in completed.items():
                    adapter = backend_factory(checked, kind, source_root, record["prepared"])
                    inventory(checked, kind, adapter, record["prepared"], record["job_seal"], record["identity"])
                require(fingerprint(backend.protected()) == checked["protected_sha256"], "PROTECTED_BASELINE_DRIFT")
                journal.append(state="OPERATION_APPLICATION_VERIFIED", effect=effect, data={"application": result})
                return {"state": "OPERATION_APPLICATION_VERIFIED", "grant_effect": effect,
                        "operation_id": checked["operation_id"], "consumer_acceptance": "PENDING_RO04"}
            except BaseException as error:
                rows = journal.events()
                # Durable pre-ambiguity intent, not stale in-memory progress, is authoritative.
                if rows:
                    effect = rows[-1]["grant_effect"]
                report = diagnostics.report(error, stage=stage, prior_effect=effect, worker=False,
                    dispatch_possible=any(row["state"] == "CREATE_OUTCOME_UNKNOWN" for row in rows))
                if rows and rows[-1]["state"] == "CERTIFICATE_OBSERVED":
                    safe = rows[-1]["data"]["certificate"]["evidence"]
                    if safe["state"] not in evidence.SUCCESS:
                        report = {key: safe[key] for key in (
                            "state", "reason", "safe_cause", "stage", "grant_effect",
                            "disposition", "consumer_acceptance")}
                unresolved = (effect == diagnostics.UNKNOWN or not rows or rows[-1]["state"] in {
                    "CREATE_OUTCOME_UNKNOWN", "RUN_OUTCOME_UNKNOWN", "EXECUTION_IDENTIFIED", "JOB_CREATED"})
                journal.append(state="STOPPED_UNRESOLVED" if unresolved else "STOPPED_WITH_KNOWN_EFFECT",
                               effect=report["grant_effect"], data={"diagnostic": report})
                return {**report, "operation_id": checked["operation_id"]}


def inspect(root, *, source_root, inspector_factory, journal_base=spec.ARTIFACTS):
    """Observe original identity; zero create/run/SQL/rollback/commit/reversal.

    Missing evidence, unknown create, running task and commit ambiguity remain
    UNKNOWN. No inference from absence, process exit, elapsed time or a caller
    supplied acceptance flag. Inspection never resumes the stage chain.
    """
    require(Path(journal_base).resolve() == spec.ARTIFACTS
            and Path(root).parent.resolve() == spec.ARTIFACTS, "FIXED_JOURNAL_BASE_REQUIRED")
    with ProgramLock(journal_base, existing=True), Journal(root) as journal:
        plan = journal.plan()
        contract.validate_plan(plan, source_root=source_root, expected_git=plan["git_sha"], inspect_only=True)
        rows = journal.events()
        require(rows, "INCOMPLETE_IMMUTABLE_OPERATION")
        creates = [row for row in rows if row["state"] == "CREATE_OUTCOME_UNKNOWN"]
        require(creates, "NO_ORIGINAL_DISPATCH_TO_INSPECT")
        original = creates[-1]
        kind, data = original["kind"], original["data"]
        predecessors = data["predecessors"]
        prepared = inspect_request(plan, kind, predecessors, data["prepared"])
        require(prepared["request_sha256"] == data["request_sha256"], "ORIGINAL_REQUEST_DRIFT")
        adapter = inspector_factory(plan, kind, source_root, prepared)
        effect = rows[-1]["grant_effect"]
        output = {"state": "INSPECTION_UNRESOLVED_NO_REPLAY", "grant_effect": effect,
                  "operation_id": plan["operation_id"], "consumer_acceptance": "PENDING_RO04"}
        try:
            job = adapter.job(kind)
            executions = adapter.executions(kind)
            run_intents = [r for r in rows[original["sequence"] + 1:]
                           if r["kind"] == kind and r["state"] == "RUN_OUTCOME_UNKNOWN"]
            if not run_intents:
                # The fsynced write-ahead journal proves this controller never
                # submitted RUN. Confirm the same pristine job and zero actual
                # executions twice; do not infer this from process exit alone.
                seal = _job(plan, kind, job, prepared, count=0)
                require(executions == [], "ORIGINAL_EXECUTION_UNCONFIRMED")
                require(_job(plan, kind, adapter.job(kind), prepared, count=0) == seal
                        and adapter.executions(kind) == [], "ORIGINAL_INVENTORY_DRIFT")
                require(fingerprint(adapter.protected()) == plan["protected_sha256"], "PROTECTED_BASELINE_DRIFT")
                output.update(state="INSPECTED_WITH_KNOWN_EFFECT", observed_stage_state="JOB_CREATED_NO_RUN_SUBMISSION")
                journal.append(state=output["state"], kind=kind, effect=effect, data=deepcopy(output))
                return output  # Never run this original job; any new attempt is distinct.
            require(type(executions) is list and len(executions) == 1, "ORIGINAL_EXECUTION_UNCONFIRMED")
            seal = spec.validate_job_export(kind, job, prepared, namespace=spec.PROJECT_NUMBER,
                expected_executions=1, operation_id=plan["operation_id"])
            known_seals = [r["data"]["job_seal"] for r in rows[original["sequence"] + 1:]
                           if r["state"] == "JOB_CREATED"]
            require(not known_seals or known_seals == [seal], "ORIGINAL_JOB_DRIFT")
            identity = _execution(plan, kind, executions[0], prepared, seal)
            known_ids = [r["data"]["identity"] for r in rows[original["sequence"] + 1:]
                         if r["state"] == "EXECUTION_IDENTIFIED"]
            require(not known_ids or known_ids == [identity], "ORIGINAL_EXECUTION_DRIFT")
            observed = adapter.execution(kind, identity["name"])
            _execution(plan, kind, observed, prepared, seal, identity)
            require(terminal(observed) is not None, "ORIGINAL_EXECUTION_STILL_RUNNING")
            certificate = adapter.certificate(kind, identity["name"])
            body, _ = _verified(plan, kind, predecessors, certificate)
            inventory(plan, kind, adapter, prepared, seal, identity)
            require(fingerprint(adapter.protected()) == plan["protected_sha256"], "PROTECTED_BASELINE_DRIFT")
            # Do not resolve UNKNOWN from weak local flags or lack of privileges.
            output.update(state=("INSPECTED_WITH_KNOWN_EFFECT" if body["grant_effect"] != diagnostics.UNKNOWN
                                 else "INSPECTION_UNRESOLVED_NO_REPLAY"),
                          grant_effect=body["grant_effect"], observed_stage_state=body["state"],
                          certificate_sha256=fingerprint(certificate))
        except BaseException as error:
            output["diagnostic"] = diagnostics.report(error, stage="transport", prior_effect=effect,
                                                        worker=False, dispatch_possible=True)
        journal.append(state=output["state"], kind=kind, effect=output["grant_effect"], data=deepcopy(output))
        return output
