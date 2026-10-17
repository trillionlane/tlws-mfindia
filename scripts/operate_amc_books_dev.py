#!/usr/bin/env python3
"""Owner-only registered whole-AMC operations; help performs no cloud access."""

import argparse
import json
import os
import sys

from mfdataindia.enrichment.book_deployment import execute
from mfdataindia.review.rollout import Manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["install", "activate", "recover", "inspect"])
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
            ),
            flush=True,
        )
    except Exception as error:
        print("AMC book operation failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
