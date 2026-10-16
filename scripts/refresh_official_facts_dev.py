#!/usr/bin/env python3
"""Run the fixed official-facts DEV job; no general URL/cohort/config overrides."""

import argparse
import json
import os
import re
from pathlib import Path
from psycopg.conninfo import conninfo_to_dict
from mfdataindia.enrichment import refresh_runtime, refresh_policy


def run(environment, root):
    configuration = json.loads((root / "config/official-facts-dev.json").read_text())
    if configuration != {
        "format": "official-facts-runtime-config-v1",
        "project": "trillionlane-dev",
        "connection": "trillionlane-dev:asia-south1:tlws-mf-data-dev",
        "database": "mfdataindia",
        "role": "mfdata_verified_generator",
        "job": refresh_runtime.JOB,
        "identity_document_id": refresh_runtime.IDENTITY_DOCUMENT,
        "identity_page": 28,
        "cohort": [103173, 103174],
        "archive_bucket": refresh_policy.ARCHIVE_BUCKET,
        "archive_prefix": refresh_policy.ARCHIVE_PREFIX,
    }:
        raise ValueError("checked-in bounded refresh configuration differs")
    dsn = environment["MFDATAINDIA_DSN"]
    parsed = conninfo_to_dict(dsn)
    if (
        parsed.get("host") != "/cloudsql/" + configuration["connection"]
        or parsed.get("dbname") != configuration["database"]
        or parsed.get("user") != configuration["role"]
        or not parsed.get("password")
        or parsed.get("hostaddr")
        or not re.fullmatch("[0-9a-f]{40}", environment["DEPLOYMENT_GIT_SHA"])
        or environment.get("CLOUD_RUN_JOB") != configuration["job"]
        or environment.get("CLOUD_RUN_TASK_INDEX") != "0"
        or environment.get("CLOUD_RUN_TASK_COUNT") != "1"
        or environment.get("CLOUD_RUN_TASK_ATTEMPT") != "0"
    ):
        raise ValueError("single-task, no-retry exact DEV runtime required")
    return refresh_runtime.execute(dsn, environment["CLOUD_RUN_EXECUTION"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        result = run(os.environ, Path(__file__).resolve().parents[1])
        print(json.dumps(result, sort_keys=True), flush=True)
        raise SystemExit(1 if result["status"] == "failed" else 0)
    except Exception as error:
        print(
            json.dumps(
                {
                    "format": "official-facts-runtime-failure-v1",
                    "error_type": type(error).__name__,
                    "source_rows_changed": 0,
                }
            ),
            flush=True,
        )
        raise SystemExit(1) from None
