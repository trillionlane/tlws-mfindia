#!/usr/bin/env python3
"""Read-only private current/refresh certification; --help has no cloud calls."""

import argparse
import json
import os
import sys
from mfdataindia.enrichment.refresh_certification import certify
from mfdataindia.review.rollout import Manifest

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--active", action="store_true")
    group.add_argument("--recovery", action="store_true")
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                certify(
                    Manifest.model_validate_json(os.environ["REVIEW_MANIFEST"]),
                    os.environ,
                    active=args.active,
                    recovery=args.recovery,
                ),
                sort_keys=True,
            )
        )
    except Exception as error:
        print("fact-refresh certification failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
