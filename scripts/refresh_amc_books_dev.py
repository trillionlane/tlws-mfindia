#!/usr/bin/env python3
"""Retain two accepted source tasks and add registered complete-AMC book tasks."""

import argparse
import json
import os
from pathlib import Path

from mfdataindia.enrichment import book_runtime, book_sources
from refresh_official_facts_dev import validate as validate_runtime


def run(environment, root):
    count = book_sources.registry(root=root)["maximum_sources_per_execution"]
    dsn = validate_runtime(environment, root, task_count=count)
    return book_runtime.execute(
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
                    "format": "official-amc-book-runtime-failure-v1",
                    "error_type": type(error).__name__,
                    "source_rows_changed": 0,
                }
            ),
            flush=True,
        )
        raise SystemExit(1) from None
