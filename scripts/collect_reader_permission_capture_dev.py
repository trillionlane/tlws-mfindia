#!/usr/bin/env python3
"""ONE separately approved read-only owner catalogue capture; no grants or app reads."""
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

from mfdataindia.review import reader_permission_capture as collector
from mfdataindia.review import reader_permission_capture_inventory as frozen
from mfdataindia.review import reader_permission_capture_spec as spec
from mfdataindia.review.evidence_transport import render_evidence
from mfdataindia.review.rollout import CONNECTION, Manifest, seal_evidence

_consumed = False


def worker_binding(manifest, environment):
    paths = {"PROGRAM": Path(__file__).resolve(), "SPEC": Path(spec.__file__).resolve(),
             "INVENTORY": Path(frozen.__file__).resolve()}
    actual = {k: hashlib.sha256(p.read_bytes()).hexdigest() for k, p in paths.items()}
    spec.require(manifest.rollout_id == spec.IDENTITY and manifest.cohort == [154117] and manifest.canary_code == 154117)
    spec.require(environment.get("DEPLOYMENT_GIT_SHA") == manifest.git_sha)
    spec.require(all(environment.get("READER_PERMISSION_CAPTURE_" + k + "_SHA256") == v for k, v in actual.items()))
    spec.require(environment.get("CLOUD_RUN_JOB") == spec.JOB)
    spec.require(re.fullmatch(re.escape(spec.JOB) + r"-[a-z0-9]{5,10}", environment.get("CLOUD_RUN_EXECUTION", "")))
    spec.require(environment.get("CLOUD_RUN_TASK_INDEX") == environment.get("CLOUD_RUN_TASK_ATTEMPT") == "0")
    spec.require(environment.get("CLOUD_RUN_TASK_COUNT") == "1")
    spec.require(not any(k.startswith("PG") for k in environment))
    package = paths["SPEC"].parents[1]
    for name, sha in spec.SOURCE_HASHES.items():
        spec.require(name.startswith("src/mfdataindia/"))
        spec.require(hashlib.sha256((package / name.removeprefix("src/mfdataindia/")).read_bytes()).hexdigest() == sha)
    return {"packaged_program_sha256": actual["PROGRAM"], "packaged_spec_sha256": actual["SPEC"],
        "packaged_inventory_sha256": actual["INVENTORY"], "manifest_image": manifest.image,
        "manifest_git_sha": manifest.git_sha}


def connection_arguments(dsn):
    spec.require(type(dsn) is str and 0 < len(dsn.encode()) <= 32768 and "\0" not in dsn)
    parsed = conninfo_to_dict(dsn)
    spec.require(set(parsed) <= {"host", "dbname", "user", "password", "port", "sslmode"})
    spec.require(parsed.get("host") == "/cloudsql/" + CONNECTION and parsed.get("dbname") == "mfdataindia")
    spec.require(parsed.get("user") == "mfdata_admin" and bool(parsed.get("password")))
    spec.require(parsed.get("port", "5432") == "5432" and parsed.get("sslmode", "disable") == "disable")
    return {"host": "/cloudsql/" + CONNECTION, "dbname": "mfdataindia", "user": "mfdata_admin",
        "password": parsed["password"], "port": "5432", "sslmode": "disable", "connect_timeout": 5,
        "autocommit": True, "prepare_threshold": None, "row_factory": dict_row}


def run_worker(environment):
    global _consumed
    spec.require(not _consumed)
    _consumed = True  # Before manifest decode, secret lookup or ambiguous connect.
    manifest = Manifest.model_validate_json(environment["REVIEW_MANIFEST"])
    binding = worker_binding(manifest, environment)
    spec.require(threading.current_thread() is threading.main_thread()
                 and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0))
    arguments = connection_arguments(environment["MFDATAINDIA_DSN"])
    conn = psycopg.connect(**arguments)  # Exactly once, no fallback/reconnect.
    failure = None
    try:
        result = collector.Capture().run(conn) | {"worker_binding": binding}
    except BaseException as error:
        failure = error
    finally:
        try:
            conn.close()  # Transport teardown only, never cleanup SQL.
        except BaseException as error:
            if failure is None:
                failure = error
    if failure is not None:
        raise failure from None
    spec.validate_result(result)
    return render_evidence(seal_evidence(manifest, spec.STAGE, result))


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(run_worker(os.environ), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("permission capture failed: " + type(error).__name__ + ";STOP_NO_RETRY", file=sys.stderr)
        raise SystemExit(1) from None
