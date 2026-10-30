#!/usr/bin/env python3
"""One fixed read-only DEV owner capture; no grant and no repeat after failure."""

import argparse
import os
from pathlib import Path
import signal
import sys
import threading

import psycopg

from mfdataindia.review import broad_reader_direct_delivery_controller as controller
from mfdataindia.review import broad_reader_direct_delivery_spec as spec
from mfdataindia.review import broad_reader_direct_grant as direct
from mfdataindia.review import broad_reader_policy as policy
from mfdataindia.review import broad_reader_source_authority as source
from mfdataindia.review.evidence_transport import render_evidence


ROOT = Path(__file__).resolve().parents[1]
_consumed = False
_STAGES = frozenset({"startup", "package", "environment", "payload", "window",
                     "dsn", "connect", "capture", "seal"})
_STAGE_TAG = object()


def _spec_stage(label, operation):
    """Tag only a source-fixed stage on the original spec error; never log it."""
    if type(label) is not str or label not in _STAGES:
        raise ValueError("UNAPPROVED_DIRECT_CAPTURE_DIAGNOSTIC_STAGE")
    try:
        return operation()
    except spec.DeliverySpecError as error:
        error._safe_direct_capture_stage = (_STAGE_TAG, label)
        raise


def _safe_error_label(error):
    if isinstance(error, spec.DeliverySpecError):
        marker = getattr(error, "_safe_direct_capture_stage", None)
        if (type(marker) is tuple and len(marker) == 2
                and marker[0] is _STAGE_TAG and type(marker[1]) is str
                and marker[1] in _STAGES):
            return "DeliverySpecError:" + marker[1]
    return type(error).__name__


def run_worker(environment):
    """Consume one exact request before opening the versioned owner secret."""
    global _consumed
    _spec_stage("startup", lambda: spec.require(
        not _consumed, "CONSUMED_DIRECT_CAPTURE_WORKER"))
    _consumed = True
    _spec_stage("startup", lambda: spec.require(
        threading.current_thread() is threading.main_thread()
        and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0),
        "EXCLUSIVE_DIRECT_CAPTURE_MAIN_THREAD_REQUIRED"))
    hashes = _spec_stage("package", lambda: controller.package_hashes(ROOT))
    packaged = _spec_stage("package", lambda: source.verify_packaged_source(ROOT))
    payload = _spec_stage("environment", lambda: spec.worker_environment(
        "capture", environment,
        final_git_sha=environment.get("DEPLOYMENT_GIT_SHA"),
        source_sha256=policy.fingerprint(packaged), **hashes))
    _spec_stage("payload", lambda: spec.validate_capture_payload(
        payload, final_git_sha=environment["DEPLOYMENT_GIT_SHA"],
        source_root=ROOT))
    _spec_stage("window", lambda: direct.direct_live(payload["window"], remaining=30))
    arguments = _spec_stage("dsn", lambda: spec.connection_arguments(
        "capture", environment["MFDATAINDIA_DSN"]))
    _spec_stage("window", lambda: direct.direct_live(payload["window"], remaining=30))
    conn = _spec_stage("connect", lambda: psycopg.connect(**arguments))
    failure = None
    result = None
    try:
        result = _spec_stage("capture", lambda: direct.capture_before(
            conn, packaged_source=packaged, source_root=ROOT,
            window=payload["window"]))
    except BaseException as error:
        failure = error
    finally:
        try:
            conn.close()  # Never a SQL cleanup or a second connection.
        except BaseException as error:
            if failure is None:
                failure = error
    if failure is not None:
        raise failure from None
    _spec_stage("seal", lambda: spec.require(
        result["grant_authorized"] is False,
        "DIRECT_CAPTURE_MUST_NOT_AUTHORIZE_GRANT"))
    certificate = _spec_stage("seal", lambda: spec.seal_certificate(
        payload_sha256=policy.fingerprint(payload),
        stage="direct-capture-readonly", evidence=result))
    return render_evidence(certificate)


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(run_worker(dict(os.environ)), flush=True)


def _main_guarded():
    """Keep the single existing type-only failure channel, with a safe stage."""
    try:
        main()
    except BaseException as error:
        # No payload, DSN, SQL, provider response or source body in stderr.
        print("direct read capture failed:" + _safe_error_label(error) +
              ";STOP_NO_RETRY", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    _main_guarded()
