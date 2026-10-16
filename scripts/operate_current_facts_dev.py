#!/usr/bin/env python3
"""Install/activate/recover current facts in DEV; --help performs no cloud calls."""

import argparse
import json
import os
import sys

from mfdataindia.enrichment.current_facts_deployment import execute
from mfdataindia.review.rollout import Manifest

if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("operation", choices=["install", "activate", "recover"])
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
        print("current-facts operation failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
