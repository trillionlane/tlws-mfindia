#!/usr/bin/env python3
"""One fixed DEV owner grant after complete capture and maximal proof."""

import argparse
import os
from pathlib import Path
import signal
import sys
import threading

import psycopg

from mfdataindia.review import broad_reader_direct_capacity as capacity
from mfdataindia.review import broad_reader_direct_delivery_controller as controller
from mfdataindia.review import broad_reader_direct_delivery_spec as spec
from mfdataindia.review import broad_reader_direct_grant as direct
from mfdataindia.review import broad_reader_policy as policy
from mfdataindia.review import broad_reader_source_authority as source
from mfdataindia.review.evidence_transport import render_evidence


ROOT = Path(__file__).resolve().parents[1]
_consumed = False


def run_worker(environment):
    """Two exact owner connections; no reconnect, retry or SQL reversal."""
    global _consumed
    spec.require(not _consumed, "CONSUMED_DIRECT_GRANT_WORKER")
    _consumed = True
    spec.require(threading.current_thread() is threading.main_thread()
                 and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0),
                 "EXCLUSIVE_DIRECT_GRANT_MAIN_THREAD_REQUIRED")
    hashes = controller.package_hashes(ROOT, kind="grant")
    packaged = source.verify_packaged_source(ROOT)
    payload = spec.worker_environment(
        "grant", environment,
        final_git_sha=environment.get("DEPLOYMENT_GIT_SHA"),
        source_sha256=policy.fingerprint(packaged), **hashes)
    document = spec.validate_grant_payload(
        payload, final_git_sha=environment["DEPLOYMENT_GIT_SHA"],
        source_root=ROOT)
    payload_sha256 = policy.fingerprint(document)
    capacity_proof = capacity.prove_capacity(
        document, payload_sha256=payload_sha256)
    policy._live(document["window"], remaining=20)
    arguments = spec.connection_arguments("grant", environment["MFDATAINDIA_DSN"])
    policy._live(document["window"], remaining=20)

    connections = []
    failure = None
    receipt = None
    rendered = None
    try:
        target = psycopg.connect(**arguments)
        connections.append(target)
        policy._live(document["window"], remaining=20)
        observer = psycopg.connect(**arguments)
        connections.append(observer)
        policy._live(document["window"], remaining=15)
        permit = direct.Permit(document, payload_sha256)
        receipt = direct.run(target, observer, permit, source_root=ROOT)
        spec.require(receipt["stage"] == "COMMITTED_STATE_VERIFIED"
                     and receipt["permission_enabled"] is False
                     and receipt["authenticated_reader_verified"] is False
                     and receipt["ti_verified"] is False,
                     "COMMITTED_UNVERIFIED")
        certificate = spec.seal_certificate(
            payload_sha256=payload_sha256,
            stage="direct-grant-transaction",
            evidence={"transaction_receipt": receipt,
                      "capacity_proof": capacity_proof})
        rendered = render_evidence(certificate)
    except BaseException as error:
        failure = error if receipt is None else direct.GrantError("COMMITTED_UNVERIFIED")
    finally:
        for connection in reversed(connections):
            try:
                connection.close()  # Transport teardown only; no SQL after ambiguity.
            except BaseException:
                if failure is None:
                    failure = direct.GrantError(
                        "COMMITTED_UNVERIFIED" if receipt is not None
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
    except BaseException as error:
        safe_labels = {
            "COMMIT_OUTCOME_UNKNOWN", "COMMITTED_UNVERIFIED",
            "PRECOMMIT_ABORTED_NO_RETRY", "WALL_ABORT_UNCONFIRMED_NO_FURTHER_IO",
            "ABORT_UNCONFIRMED_NO_RETRY", "OBSERVER_ABORT_UNCONFIRMED_NO_RETRY",
        }
        label = (str(error) if isinstance(error, direct.GrantError)
                 and str(error) in safe_labels else type(error).__name__)
        print("direct owner grant failed:" + label + ";STOP_NO_RETRY",
              file=sys.stderr)
        raise SystemExit(1) from None
