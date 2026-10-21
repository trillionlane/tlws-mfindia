#!/usr/bin/env python3
"""Install/append/inspect bounded monthly facts; --help makes no cloud calls."""

import argparse
import json
import os
import sys

from mfdataindia.enrichment.monthly_deployment import execute
from mfdataindia.review.rollout import Manifest
from mfdataindia.enrichment.pdf_source_dispatch import require_source_authority

if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("operation", choices=["install", "append", "inspect"])
        args = parser.parse_args()
        if args.operation == "append":
            require_source_authority()
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
        print("monthly facts operation failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
