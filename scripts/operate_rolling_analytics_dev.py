#!/usr/bin/env python3
"""Explicit owner install/register or restricted generator; no source DML."""

import json
import os
import sys
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from mfdataindia.analytics import rolling_operations as operations, rolling_store as store
from mfdataindia.review import store as review
from mfdataindia.review.privileges import verify_runtime
from mfdataindia.review.rollout import CONNECTION, Manifest, seal_evidence, verify_evidence


def execute(manifest, environment, root):
    if environment["DEPLOYMENT_GIT_SHA"] != manifest.git_sha:
        raise ValueError("rolling job source differs")
    operation = environment["ROLLING_OPERATION"]
    parsed = conninfo_to_dict(environment["MFDATAINDIA_DSN"])
    expected_role = "mfdata_verified_generator" if operation == "generate" else "mfdata_admin"
    if (
        parsed.get("host") != "/cloudsql/" + CONNECTION
        or parsed.get("dbname") != "mfdataindia"
        or parsed.get("user") != expected_role
        or not parsed.get("password")
        or parsed.get("hostaddr")
    ):
        raise ValueError("rolling workload credential differs from pinned DEV database/principal")
    if operation == "generate":
        with psycopg.connect(environment["MFDATAINDIA_DSN"], row_factory=dict_row) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            verify_runtime(conn, "generator")
            store.verify_acl(conn)
            snapshots = [review.capture(conn, c) for c in sorted(manifest.cohort)]
        with psycopg.connect(environment["MFDATAINDIA_DSN"], row_factory=dict_row) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            verify_runtime(conn, "generator")
            store.verify_acl(conn)
            values = store.publish(conn, snapshots)
            if store.publish(conn, snapshots) != values:
                raise ValueError("rolling replay differs")
        result = {
            "results": [
                {k: r[k] for k in ("code", "result_id", "input_hash", "policy_hash")}
                for r in values
            ]
        }
    elif operation in ("install", "register"):
        with psycopg.connect(environment["MFDATAINDIA_DSN"], row_factory=dict_row) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            if operation == "install":
                result = operations.install(conn, root)
            else:
                record = json.loads(environment["ROLLING_RECORD"])
                previous = Manifest.model_validate(record["manifest"])
                evidence = verify_evidence(previous, "rolling-http", record["certificate"])
                if (
                    evidence.get("level") != "DEV_PRIVATE_HTTP"
                    or evidence.get("origin") != evidence.get("audience")
                    or evidence.get("source_writes") != 0
                    or evidence.get("records_appended") != 0
                ):
                    raise ValueError(
                        "rolling registration requires verified service-origin read-only certificate"
                    )
                result = {"certificate_id": store.register(conn, record)}
    else:
        raise ValueError("unsupported rolling operation")
    return seal_evidence(
        manifest, "rolling-" + operation, {"level": "DEV_DATABASE", "source_writes": 0, **result}
    )


if __name__ == "__main__":
    try:
        m = Manifest.model_validate_json(os.environ["REVIEW_MANIFEST"])
        print(json.dumps(execute(m, os.environ, Path("/app")), sort_keys=True))
    except Exception as exc:
        print("rolling operation failed: " + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
