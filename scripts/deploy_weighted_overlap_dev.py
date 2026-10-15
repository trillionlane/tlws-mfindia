#!/usr/bin/env python3
"""Explicit DEV immutable overlap install/publish. --help is dormant."""

import argparse
import json
import os
import sys
from mfdataindia.analytics.overlap_deployment import execute
from mfdataindia.review.rollout import Manifest

if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("operation", choices=["install", "publish"])
        args = parser.parse_args()
        print(
            json.dumps(
                execute(
                    Manifest.model_validate_json(os.environ["REVIEW_MANIFEST"]),
                    os.environ,
                    args.operation,
                ),
                sort_keys=True,
            )
        )
    except Exception as error:
        print("weighted overlap operation failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
