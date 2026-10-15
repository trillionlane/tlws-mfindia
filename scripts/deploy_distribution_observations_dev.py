#!/usr/bin/env python3
"""Explicit pinned DEV observation install/append. --help performs no calls."""

import argparse
import json
import os
import sys
import requests
from mfdataindia.enrichment.distribution_deployment import execute
from mfdataindia.review.rollout import Manifest

if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("operation", choices=["install", "append"])
        args = parser.parse_args()
        with requests.Session() as session:
            result = execute(
                Manifest.model_validate_json(os.environ["REVIEW_MANIFEST"]),
                os.environ,
                args.operation,
                session,
            )
        print(json.dumps(result, sort_keys=True))
    except Exception as error:
        print("distribution operation failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
