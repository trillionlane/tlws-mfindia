#!/usr/bin/env python3
"""Read-only private monthly facts certification; --help makes no cloud calls."""

import argparse
import json
import os
import sys

from mfdataindia.enrichment.monthly_certification import certify
from mfdataindia.review.rollout import Manifest

if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--recovery", action="store_true")
        args = parser.parse_args()
        print(
            json.dumps(
                certify(
                    Manifest.model_validate_json(os.environ["REVIEW_MANIFEST"]),
                    os.environ,
                    recovery=args.recovery,
                ),
                sort_keys=True,
            )
        )
    except Exception as error:
        print("monthly facts certification failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
