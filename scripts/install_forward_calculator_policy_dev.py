#!/usr/bin/env python3
"""Owner-only fixed DEV policy install; no per-gate mode or scenario persistence."""

import json
import os
import sys

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from mfdataindia.calculators.store import install_policy
from mfdataindia.review.rollout import CONNECTION, Manifest, seal_evidence


def execute(manifest, environment):
    if environment["DEPLOYMENT_GIT_SHA"] != manifest.git_sha:
        raise ValueError("calculator installer source differs")
    parsed = conninfo_to_dict(environment["MFDATAINDIA_DSN"])
    if (
        parsed.get("host") != "/cloudsql/" + CONNECTION
        or parsed.get("dbname") != "mfdataindia"
        or parsed.get("user") != "mfdata_admin"
        or not parsed.get("password")
        or parsed.get("hostaddr")
    ):
        raise ValueError("calculator installer must target fixed DEV database/owner")
    with psycopg.connect(environment["MFDATAINDIA_DSN"], row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        conn.execute("SET LOCAL lock_timeout='5s'")
        conn.execute("SET LOCAL statement_timeout='30s'")
        conn.execute("SET LOCAL transaction_timeout='60s'")
        result = install_policy(conn)
    return seal_evidence(manifest, "forward-policy-install", {"level": "DEV_DATABASE", **result})


if __name__ == "__main__":
    try:
        m = Manifest.model_validate_json(os.environ["REVIEW_MANIFEST"])
        print(json.dumps(execute(m, os.environ), sort_keys=True))
    except Exception as exc:
        print("calculator policy install failed: " + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
