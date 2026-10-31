"""Actual one-use capture/grant/reader worker, with truthful sealed failures."""
from dataclasses import dataclass
import os
from pathlib import Path
import signal
import sys
import threading

import psycopg

from . import broad_reader_direct_delivery_spec as spec
from . import broad_reader_direct_grant as direct
from . import broad_reader_direct_reader_check as reader
from . import broad_reader_source_authority as source
from . import reader_access_profile as profiles
from . import reader_execution_contract as contract
from . import reader_execution_diagnostics as diagnostics
from . import reader_execution_evidence as evidence
from .evidence_transport import render_evidence
from .policy import canonical, fingerprint


@dataclass(frozen=True)
class Result:
    certificate: dict | None
    exit_code: int
    diagnostic: dict


class Worker:
    def __init__(self):
        self.used = False
        self.lock = threading.Lock()

    def run(self, environment, *, source_root, connect=None):
        with self.lock:
            contract.require(not self.used, "CONSUMED_READER_EXECUTION_WORKER")
            self.used = True
        connect = psycopg.connect if connect is None else connect
        stage = "validation"
        plan = kind = payload = prior = None
        predecessors = []
        connections = []
        effect = diagnostics.NO_GRANT
        grant_started = False
        failure = sealed = None
        try:
            contract.require(type(environment) is dict
                and threading.current_thread() is threading.main_thread()
                and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), "EXCLUSIVE_WORKER_CONTEXT_REQUIRED")
            kind = environment.get("BROAD_DIRECT_ROLE")
            operation = environment.get("READER_EXECUTION_OPERATION_ID")
            contract.require(operation is not None, "CURRENT_OPERATION_ID_REQUIRED")
            spec.role_for(kind, operation_id=operation)
            packaged = source.verify_packaged_source(source_root)
            payload = spec.worker_environment(kind, environment, operation_id=operation,
                final_git_sha=environment.get("DEPLOYMENT_GIT_SHA"),
                source_sha256=fingerprint(packaged), **contract.package_hashes(source_root))
            contract.validate_stage(payload, source_root=source_root,
                                     expected_git=environment["DEPLOYMENT_GIT_SHA"])
            plan, predecessors = payload["plan"], payload["predecessors"]
            prior = evidence.verify_chain(plan, kind, predecessors)
            effect = prior["prior_effect"] if prior is not None else diagnostics.NO_GRANT
            capacity = (evidence.prove_capacity(plan, prior["capture"], predecessors)
                        if kind == "grant" else None)
            remaining = {"capture": 30, "grant": 20, "reader": 15}[kind]
            direct.direct_live(plan["window"], remaining=remaining)
            stage = "dsn"
            arguments = spec.connection_arguments(kind, environment["MFDATAINDIA_DSN"])
            direct.direct_live(plan["window"], remaining=remaining)
            stage = "connect"
            connection = connect(**arguments)
            connections.append(connection)
            if kind == "capture":
                stage = "capture"
                captured = direct.capture_before(connection, packaged_source=packaged,
                    source_root=source_root, window=plan["window"])
                compact = evidence.compact_capture(captured)
                restored = evidence.restore_capture(compact, plan)
                contract.require(restored == captured, "COMPLETE_CAPTURE_HASH_MISMATCH")
                no_op = not restored["before_snapshot"]["plan"]["sql"]
                sealed = evidence.certificate(plan, kind, predecessors,
                    state=evidence.NOOP if no_op else evidence.CAPTURED,
                    effect=effect, data={"capture": compact})
            elif kind == "grant":
                direct.direct_live(plan["window"], remaining=20)
                observer = connect(**arguments)
                connections.append(observer)
                direct.direct_live(plan["window"], remaining=15)
                document = evidence.direct_document(plan, prior["capture"],
                    intent_sha256=payload["create_only_intent_sha256"])
                permit = direct.Permit(document, fingerprint(document))
                stage = "grant"
                grant_started = True
                receipt = direct.run(connection, observer, permit, source_root=source_root)
                contract.require(receipt["stage"] == "COMMITTED_STATE_VERIFIED", "COMMITTED_UNVERIFIED")
                effect = diagnostics.VERIFIED  # Never erase a confirmed commit on later failure.
                sealed = evidence.certificate(plan, kind, predecessors, state=evidence.GRANTED,
                    effect=effect, data={"transaction_receipt": receipt,
                        "planned_after_sha256": fingerprint(prior["planned_after"]), "capacity_proof": capacity})
                evidence.verify_grant(plan, predecessors,
                    evidence.read_certificate(plan, kind, predecessors, sealed), prior["capture"])
            else:
                stage = "reader"
                result = reader.run(connection, planned_after=prior["planned_after"],
                                    reader_profile=profiles.BROAD)
                sealed = evidence.certificate(plan, kind, predecessors, state=evidence.READ,
                    effect=effect, data={"reader_result": result})
                evidence.verify_reader(plan, predecessors, sealed)
            stage = "certificate"
            render_evidence(sealed)  # Complete transport verification before any success output.
        except BaseException as error:
            failure = error
        finally:
            for connection in reversed(connections):
                try:
                    connection.close()  # Transport close only; no further SQL/reconnect/rollback.
                except BaseException as error:
                    if failure is None:
                        failure = error
                        stage = "transport"
        if failure is None:
            return Result(sealed, 0, {"state": sealed["evidence"]["state"],
                                      "grant_effect": effect, "consumer_acceptance": "PENDING_RO04"})
        diagnostic = diagnostics.report(failure, stage=stage, prior_effect=effect,
                                         grant_started=grant_started)
        sealed = None
        if plan is not None and kind in contract.KINDS:
            try:
                sealed = evidence.certificate(plan, kind, predecessors, state=diagnostic["state"],
                    effect=diagnostic["grant_effect"], data={}, diagnostic=diagnostic)
            except BaseException:
                pass  # No malformed or partial certificate; original safe ambiguity remains.
        return Result(sealed, 1, diagnostic)


_worker = Worker()


def main(*, source_root=None):
    root = Path(source_root) if source_root is not None else Path(__file__).resolve().parents[3]
    result = _worker.run(dict(os.environ), source_root=root)
    if result.certificate is not None:
        print(render_evidence(result.certificate), flush=True)
    if result.exit_code:
        print(canonical(result.diagnostic), file=sys.stderr, flush=True)
        raise SystemExit(result.exit_code)
