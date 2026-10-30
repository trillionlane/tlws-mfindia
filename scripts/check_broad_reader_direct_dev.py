#!/usr/bin/env python3
"""One authenticated DEV reader check; no owner credential or grant."""

import argparse
import os
from pathlib import Path
import signal
import sys
import threading

import psycopg

from mfdataindia.review import broad_reader_direct_delivery_controller as controller
from mfdataindia.review import broad_reader_direct_delivery_spec as spec
from mfdataindia.review import broad_reader_direct_reader_check as reader
from mfdataindia.review import broad_reader_direct_grant as direct
from mfdataindia.review import broad_reader_policy as policy
from mfdataindia.review import broad_reader_source_authority as source
from mfdataindia.review.evidence_transport import render_evidence


ROOT = Path(__file__).resolve().parents[1]
_consumed = False


def run_worker(environment):
    global _consumed
    spec.require(not _consumed, "CONSUMED_DIRECT_READER_WORKER")
    _consumed = True
    spec.require(threading.current_thread() is threading.main_thread()
                 and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0),
                 "EXCLUSIVE_DIRECT_READER_MAIN_THREAD_REQUIRED")
    hashes = controller.package_hashes(ROOT, kind="reader")
    packaged = source.verify_packaged_source(ROOT)
    payload = spec.worker_environment(
        "reader", environment,
        final_git_sha=environment.get("DEPLOYMENT_GIT_SHA"),
        source_sha256=policy.fingerprint(packaged), **hashes)
    document = spec.validate_reader_payload(
        payload, final_git_sha=environment["DEPLOYMENT_GIT_SHA"],
        source_root=ROOT)
    direct.direct_live(document["window"], remaining=15)
    arguments = spec.connection_arguments("reader", environment["MFDATAINDIA_DSN"])
    direct.direct_live(document["window"], remaining=15)
    conn = psycopg.connect(**arguments)  # One reader connection, no owner role.
    failure = None
    result = None
    try:
        result = reader.run(conn, planned_after=document["planned_after"])
    except BaseException as error:
        failure = error
    finally:
        try:
            conn.close()
        except BaseException as error:
            if failure is None:
                failure = error
    if failure is not None:
        raise failure from None
    spec.require(result["representative_select_completed"] is True
                 and result["permission_enabled"] is False
                 and result["ti_verified"] is False,
                 "AUTHENTICATED_READER_RESULT_DRIFT")
    marker = direct.custody_marker(document["window"])
    if marker is not None:
        result["custody"] = marker
    certificate = spec.seal_certificate(
        payload_sha256=policy.fingerprint(document),
        stage="direct-authenticated-reader",
        evidence={"reader_result": result,
                  "grant_receipt_sha256": document["grant_receipt_sha256"]})
    return render_evidence(certificate)


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(run_worker(os.environ), flush=True)


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:
        print("direct authenticated reader check failed:" + type(error).__name__
              + ";STOP_NO_RETRY", file=sys.stderr)
        raise SystemExit(1) from None
