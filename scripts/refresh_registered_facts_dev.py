#!/usr/bin/env python3
"""Use the checked-in two-source registry, never caller URL/cohort overrides."""

import argparse
import json
import os
from pathlib import Path
from mfdataindia.enrichment.pdf_source_dispatch import require_source_authority
from refresh_official_facts_dev import validate as validate_pilot_runtime


def run(environment, root):
    require_source_authority()
    from mfdataindia.enrichment import multi_runtime

    # Retain the existing single-task, pinned CloudSQL/role/SHA/job checks.
    dsn = validate_pilot_runtime(environment, root, task_count=2)
    return multi_runtime.execute(
        dsn, environment["CLOUD_RUN_EXECUTION"], task_index=int(environment["CLOUD_RUN_TASK_INDEX"])
    )


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
                    "format": "registered-facts-runtime-failure-v1",
                    "error_type": type(error).__name__,
                    "source_rows_changed": 0,
                }
            ),
            flush=True,
        )
        raise SystemExit(1) from None
