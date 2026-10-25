#!/usr/bin/env python3
"""ONE separately approved fixed owner grant; never an implicit activation."""
import argparse
import hashlib
import os
from pathlib import Path
import re
import signal
import sys
import threading

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from mfdataindia.review import reader_access_correction as engine
from mfdataindia.review import reader_access_profile_v1 as profile
from mfdataindia.review import reader_access_transport_spec as spec
from mfdataindia.review import reader_access_owner_connection as guarded
from mfdataindia.review import reader_access_transport_controller as core
from mfdataindia.review.evidence_transport import render_evidence
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.rollout import CONNECTION, Manifest, seal_evidence

_consumed = False


def worker_binding(payload, environment):
    spec.validate_payload(payload)
    m = Manifest.model_validate(payload["manifest"])
    spec.require(spec.parse(environment["REVIEW_MANIFEST"]) == payload["manifest"])
    return packaged_binding(m, payload["controller_sha256"], environment)


def packaged_binding(m, controller_sha256, environment):
    """File/platform binding only: never a connection or approval entrypoint."""
    spec.require(m.rollout_id == spec.IDENTITY and m.cohort == [154117] and m.canary_code == 154117)
    spec.digest(controller_sha256)
    paths = {"program": Path(__file__).resolve(), "spec": Path(spec.__file__).resolve(),
             "adapter": Path(guarded.__file__).resolve(), "controller": Path(core.__file__).resolve(),
             "engine": Path(engine.__file__).resolve(), "profile": Path(profile.__file__).resolve()}
    actual = {k: hashlib.sha256(v.read_bytes()).hexdigest() for k, v in paths.items()}
    spec.require(actual["engine"] == spec.ENGINE_SHA and actual["profile"] == spec.PROFILE_SHA)
    spec.require(actual["controller"] == controller_sha256)
    spec.require(actual["program"] == core.WORKER_SHA and actual["spec"] == core.SPEC_SHA
                 and actual["adapter"] == core.ADAPTER_SHA)
    spec.require(environment.get("DEPLOYMENT_GIT_SHA") == m.git_sha)
    spec.require(environment.get("CLOUD_RUN_JOB") == spec.JOB)
    spec.require(re.fullmatch(re.escape(spec.JOB) + r"-[a-z0-9]{5,10}", environment.get("CLOUD_RUN_EXECUTION", "")) is not None)
    spec.require(environment.get("CLOUD_RUN_TASK_INDEX") == environment.get("CLOUD_RUN_TASK_ATTEMPT") == "0")
    spec.require(environment.get("CLOUD_RUN_TASK_COUNT") == "1")
    spec.require(not any(k.startswith("PG") for k in environment))
    return {"packaged_" + k + "_sha256": v for k, v in actual.items()} | {
        "manifest_image": m.image, "manifest_git_sha": m.git_sha}


def expected_result(payload, binding):
    approval = core.approval_document(payload)
    return {"engine_receipt": spec.expected_engine_receipt(payload),
            "payload_sha256": fingerprint(payload), "request_sha256": approval["request_sha256"],
            "approval_sha256": fingerprint(approval), "lock_witness": payload["grant_proposal"]["lock_witness"],
            "worker_binding": binding, "performance_or_FS13_acceptance": False,
            "postcommit_permanence_guarantee": False}


def connection_arguments(dsn):
    spec.require(type(dsn) is str and 0 < len(dsn.encode()) <= 32768 and "\0" not in dsn)
    parsed = conninfo_to_dict(dsn)
    spec.require(set(parsed) <= {"host", "dbname", "user", "password", "port", "sslmode"})
    spec.require(parsed.get("host") == "/cloudsql/" + CONNECTION and parsed.get("dbname") == "mfdataindia")
    spec.require(parsed.get("user") == engine.OWNER and bool(parsed.get("password")))
    spec.require(parsed.get("port", "5432") == "5432" and parsed.get("sslmode", "disable") == "disable")
    return {"host": "/cloudsql/" + CONNECTION, "dbname": "mfdataindia", "user": engine.OWNER,
            "password": parsed["password"], "port": "5432", "sslmode": "disable",
            "connect_timeout": 5, "autocommit": True, "prepare_threshold": None, "row_factory": dict_row}


def run_worker(environment):
    global _consumed
    spec.require(not _consumed)
    _consumed = True  # Consume before decoding, secret lookup or ambiguous connection I/O.
    payload = spec.decode_payload(environment)
    binding = worker_binding(payload, environment)
    spec.require(threading.current_thread() is threading.main_thread()
                 and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0))
    m = Manifest.model_validate(payload["manifest"])
    window = payload["grant_proposal"]["window"]
    spec.live_window(window)
    expected = expected_result(payload, binding)
    # Success is deterministic: reject an unreportable complete certificate
    # BEFORE any secret/connection/write. No truncation or optimistic commit.
    rendered = render_evidence(seal_evidence(m, spec.STAGE, expected))
    arguments = connection_arguments(environment["MFDATAINDIA_DSN"])
    spec.live_window(window)
    conn = psycopg.connect(**arguments)  # One connection, no retry/fallback.
    failure = None
    result = None
    try:
        spec.live_window(window, remaining=15)
        transport = guarded.guarded_connection(conn, payload["grant_proposal"])
        permit = engine.Permit(payload["grant_proposal"]["change"], fingerprint(payload["grant_proposal"]["change"]))
        result = engine.run_change(transport, permit)
        if result != expected["engine_receipt"] or transport.lock_witness != expected["lock_witness"]:
            raise engine.ChangeFailure("COMMITTED_UNVERIFIED")
    except BaseException as error:
        failure = error
    finally:
        try:
            conn.close()  # Transport teardown only: never SQL/reconnect/reversal after failure.
        except BaseException:
            # Do not erase the original commit-ambiguity marker with teardown.
            if failure is None:
                failure = engine.ChangeFailure("COMMITTED_UNVERIFIED" if result is not None
                                               else "TRANSPORT_TEARDOWN_UNCONFIRMED_NO_RETRY")
    if failure is not None:
        raise failure from None
    return rendered


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(run_worker(os.environ), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        safe = str(error) if isinstance(error, engine.ChangeFailure) and str(error) in {
            "COMMIT_OUTCOME_UNKNOWN", "COMMITTED_UNVERIFIED", "PRECOMMIT_ABORTED_NO_RETRY",
            "PRECOMMIT_ABORT_UNCONFIRMED_WALL_TIMEOUT_NO_RETRY", "PRECOMMIT_ROLLBACK_UNCONFIRMED"} else type(error).__name__
        print("owner grant failed:" + safe + ";STOP_NO_RETRY", file=sys.stderr)
        raise SystemExit(1) from None
