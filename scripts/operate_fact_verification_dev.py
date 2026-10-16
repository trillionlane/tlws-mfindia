#!/usr/bin/env python3
"""Install/inspect private human-verification backend, never create a human review."""

import argparse
import json
import os
import sys
from mfdataindia.enrichment.fact_review_deployment import execute
from mfdataindia.review.rollout import Manifest

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["install", "inspect"])
    args = parser.parse_args()
    try:
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
        print("fact-verification operation failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
